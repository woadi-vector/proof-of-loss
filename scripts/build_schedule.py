"""Turn SAM runs into a schedule of loss.

Reads every runs/*/items.json written by test_sam.py, crops each detected
object out of its photo, merges the same belonging across photos, and writes:

    out/schedule.csv      the claim document, one line per item
    out/items.json        every item with its detections and merge reasons
    out/crops/            the evidence image for every detection

Usage (from the repo folder):
    python scripts/build_schedule.py
    python scripts/build_schedule.py --entries my_entries.json --life life_table.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pol.merge import merge  # noqa: E402
from pol.model import Detection  # noqa: E402
from pol.photos import crop, photo_date  # noqa: E402
from pol.schedule import ClaimantEntry, build_rows, load_life_table, totals, write_csv  # noqa: E402


def load_detections(runs_dir: Path, crops_dir: Path) -> list[Detection]:
    dets: list[Detection] = []
    dates: dict[str, date | None] = {}
    for items_file in sorted(runs_dir.glob("*/items.json")):
        for n, rec in enumerate(json.loads(items_file.read_text(encoding="utf-8"))):
            photo = rec.get("photo")
            if not photo or not Path(photo).exists():
                continue   # runs from before photo paths were recorded
            if photo not in dates:
                dates[photo] = photo_date(photo)
            det_id = f"{Path(photo).stem}:{rec['prompt'].replace(' ', '_')}:{rec['object_id']}"
            box = tuple(rec["box"])
            dets.append(Detection(
                det_id=det_id, photo=photo, photo_date=dates[photo], category=rec["prompt"],
                box=box, confidence=rec.get("confidence"),
                crop=crop(photo, box, crops_dir / f"{det_id.replace(':', '_')}.jpg"),
            ))
    # The same photo may have been run twice; keep one detection per id.
    unique = {d.det_id: d for d in dets}
    return list(unique.values())


def load_entries(path: str | None) -> dict[str, ClaimantEntry]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for item_id, e in raw.items():
        if e.get("purchase_date"):
            e["purchase_date"] = date.fromisoformat(e["purchase_date"])
        out[item_id] = ClaimantEntry(**e)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--entries", help="JSON of claimant details keyed by item_id")
    ap.add_argument("--life", help="JSON of useful-life years by category, overriding defaults")
    args = ap.parse_args()

    out = Path(args.out)
    dets = load_detections(Path(args.runs), out / "crops")
    if not dets:
        sys.exit("No detections found. Run scripts/test_sam.py on some photos first.")

    items, decisions = merge(dets)
    rows = build_rows(items, load_entries(args.entries), load_life_table(args.life))
    write_csv(rows, out / "schedule.csv")

    (out / "items.json").write_text(json.dumps([{
        "item_id": it.item_id, "category": it.category,
        "earliest_date": it.earliest_date.isoformat() if it.earliest_date else None,
        "photos": it.photos, "merge_notes": it.merge_notes,
        "detections": [{"det_id": d.det_id, "photo": d.photo, "box": d.box,
                        "confidence": d.confidence, "crop": d.crop} for d in it.detections],
    } for it in items], indent=2), encoding="utf-8")

    review = [p for p in decisions if p.outcome == "review"]
    t = totals(rows)
    print(f"{len(dets)} detections -> {len(items)} items, {len(review)} pairs awaiting model review")
    print(f"{t['priced_lines']} priced, {t['unpriced_lines']} need a replacement cost")
    print(f"Schedule: {out / 'schedule.csv'}")


if __name__ == "__main__":
    main()
