"""Score the pipeline against hand labels: found, missed, wrong, and merges.

Labels live in one CSV, one row per thing in a photo:

    status    kept      the pipeline put this detection on the schedule
              rejected  Muse Spark verification dropped it
              missed    the labeller added it: a belonging the pipeline never found
    correct   yes / no  is this detection really a belonging of its category?
                        (kept: no = false find; rejected: yes = wrongly dropped)
    item_tag  the labeller's name for the real object, the same in every photo
              it appears in ("grey-couch"). Prefilled with the pipeline's item id.

The number that matters most for a claim is item recall: a belonging missed in
one photo but found in another still makes the schedule.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

COLUMNS = ["photo", "det_id", "category", "confidence", "status", "correct", "item_tag", "crop", "note"]


def init_rows(items: list[dict], rejected: list[dict], existing: list[dict] | None = None) -> list[dict]:
    """A label sheet prefilled from out/items.json and out/rejected.json.

    Rows already labelled in `existing` are kept as they are (matched on det_id),
    and hand-added missed rows are carried over, so re-running the pipeline does
    not throw away labelling work."""
    old = {r["det_id"]: r for r in existing or [] if r.get("det_id")}
    rows = []
    for it in items:
        for d in it["detections"]:
            rows.append(old.get(d["det_id"]) or {
                "photo": Path(d["photo"]).name, "det_id": d["det_id"], "category": it["category"],
                "confidence": f"{d['confidence']:.2f}" if d.get("confidence") is not None else "",
                "status": "kept", "correct": "", "item_tag": it["item_id"], "crop": d.get("crop") or "",
                "note": ""})
    for d in rejected:
        rows.append(old.get(d["det_id"]) or {
            "photo": Path(d["photo"]).name, "det_id": d["det_id"], "category": d["category"],
            "confidence": f"{d['confidence']:.2f}" if d.get("confidence") is not None else "",
            "status": "rejected", "correct": "", "item_tag": "", "crop": d.get("crop") or "",
            "note": d.get("reason", "")})
    rows += [r for r in existing or [] if r.get("status") == "missed"]
    rows.sort(key=lambda r: (r["photo"], r["status"] == "missed", r["category"], r["det_id"]))
    return rows


def read_labels(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:   # Excel saves a BOM
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def write_labels(rows: list[dict], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def problems(rows: list[dict]) -> list[str]:
    """Rows the scorer cannot use yet."""
    out = []
    for n, r in enumerate(rows, 2):       # line 1 is the header
        s, c = r.get("status"), r.get("correct", "").lower()
        if s not in ("kept", "rejected", "missed"):
            out.append(f"line {n}: status must be kept, rejected or missed")
        elif s != "missed" and c not in ("yes", "no"):
            out.append(f"line {n} ({r.get('det_id')}): correct must be yes or no")
        elif (s == "missed" or c == "yes") and not r.get("item_tag"):
            out.append(f"line {n}: a real belonging needs an item_tag")
        elif s == "missed" and not (r.get("photo") and r.get("category")):
            out.append(f"line {n}: a missed row needs photo and category")
    return out


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def score(rows: list[dict], items: list[dict]) -> dict:
    """Compare labels with the pipeline's items. Rows must pass `problems` first."""
    pred_item = {d["det_id"]: it["item_id"] for it in items for d in it["detections"]}
    yes = lambda r: r.get("correct", "").lower() == "yes"   # noqa: E731

    found = [r for r in rows if r["status"] == "kept" and yes(r)]
    false_finds = [r for r in rows if r["status"] == "kept" and not yes(r)]
    wrongly_rejected = [r for r in rows if r["status"] == "rejected" and yes(r)]
    rightly_rejected = [r for r in rows if r["status"] == "rejected" and not yes(r)]
    missed = [r for r in rows if r["status"] == "missed"]
    real = found + wrongly_rejected + missed            # every true sighting of a belonging

    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"found": 0, "missed": 0, "false": 0})
    for r in found:
        by_cat[r["category"]]["found"] += 1
    for r in wrongly_rejected + missed:
        by_cat[r["category"]]["missed"] += 1
    for r in false_finds:
        by_cat[r["category"]]["false"] += 1

    # Items: a belonging is on the claim if any one of its sightings was found.
    true_items = {r["item_tag"] for r in real}
    claimed_items = {r["item_tag"] for r in found}
    lines_per_tag: dict[str, set[str]] = defaultdict(set)
    for r in found:
        if r["det_id"] in pred_item:
            lines_per_tag[r["item_tag"]].add(pred_item[r["det_id"]])
    real_lines = {pred_item[r["det_id"]] for r in found if r["det_id"] in pred_item}
    false_lines = {it["item_id"] for it in items} - real_lines

    # Merges: every pair of true finds of one category in two different photos.
    good_merge = wrong_merge = missed_merge = 0
    for i, a in enumerate(found):
        for b in found[i + 1:]:
            if a["photo"] == b["photo"] or a["category"] != b["category"]:
                continue
            same_truth = a["item_tag"] == b["item_tag"]
            same_pred = pred_item.get(a["det_id"]) == pred_item.get(b["det_id"])
            if same_truth and same_pred:
                good_merge += 1
            elif same_pred:
                wrong_merge += 1
            elif same_truth:
                missed_merge += 1

    return {
        "photos": len({r["photo"] for r in rows}),
        "sightings": {"real": len(real), "found": len(found), "missed_by_sam": len(missed),
                      "wrongly_rejected": len(wrongly_rejected), "false_finds": len(false_finds),
                      "rightly_rejected": len(rightly_rejected),
                      "recall": _ratio(len(found), len(real)),
                      "precision": _ratio(len(found), len(found) + len(false_finds))},
        "items": {"real": len(true_items), "on_schedule": len(claimed_items),
                  "missing_from_claim": sorted(true_items - claimed_items),
                  "recall": _ratio(len(claimed_items), len(true_items))},
        "schedule_lines": {"total": len(items), "false": len(false_lines),
                           "duplicates": sum(len(v) - 1 for v in lines_per_tag.values())},
        "merges": {"correct": good_merge, "wrong": wrong_merge, "missed": missed_merge},
        "by_category": dict(sorted(by_cat.items())),
    }


def cost(usage_log: str | Path, photos: set[str]) -> dict:
    """API spend on these photos from runs/usage.jsonl. Re-runs are counted too."""
    calls: dict[str, int] = defaultdict(int)
    total, priced, seen = 0.0, True, set()
    path = Path(usage_log)
    if not path.exists():
        return {"calls": {}, "total": None, "per_photo": None, "photos_logged": 0}
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("photo") not in photos:
            continue
        calls[rec["model"]] += 1
        seen.add(rec["photo"])
        if rec.get("cost") is None:
            priced = False
        else:
            total += rec["cost"]
    return {"calls": dict(calls), "total": round(total, 4) if priced else None,
            "per_photo": round(total / len(seen), 4) if priced and seen else None,
            "photos_logged": len(seen)}
