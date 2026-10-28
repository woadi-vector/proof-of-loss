"""Measure the pipeline against hand labels.

Usage (from the repo folder), after build_schedule.py --describe:
    python scripts/evaluate.py --init         write labels.csv, prefilled from out/
    (fill in labels.csv: correct yes/no, fix item_tag, add a "missed" row per belonging not found)
    python scripts/evaluate.py                score it; writes out/evaluation.json

labels.csv names belongings and photos, so it lives in out/ (gitignored) by default.
See pol/evaluate.py for what each column means.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pol.api import USAGE_LOG  # noqa: E402
from pol.evaluate import cost, init_rows, problems, read_labels, score, write_labels  # noqa: E402


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--labels", help="label sheet (default: out/labels.csv)")
    ap.add_argument("--init", action="store_true", help="create or refresh the label sheet")
    args = ap.parse_args()

    out = Path(args.out)
    labels = Path(args.labels) if args.labels else out / "labels.csv"
    items = json.loads((out / "items.json").read_text(encoding="utf-8"))
    rejected_file = out / "rejected.json"
    rejected = json.loads(rejected_file.read_text(encoding="utf-8")) if rejected_file.exists() else []

    if args.init:
        existing = read_labels(labels) if labels.exists() else []
        rows = init_rows(items, rejected, existing)
        write_labels(rows, labels)
        print(f"{len(rows)} rows ({len(existing)} carried over) -> {labels}")
        print("Fill in: correct (yes/no), item_tag (same name for the same object in every photo),")
        print("and one row with status 'missed' for each belonging the pipeline did not find.")
        return

    rows = read_labels(labels)
    bad = problems(rows)
    if bad:
        print(f"{len(bad)} rows need labels before scoring:")
        for b in bad[:20]:
            print(f"  {b}")
        sys.exit(1)

    s = score(rows, items)
    s["cost"] = cost(USAGE_LOG, {r["photo"] for r in rows})
    (out / "evaluation.json").write_text(json.dumps(s, indent=2), encoding="utf-8")

    sg, it, ln, mg, c = s["sightings"], s["items"], s["schedule_lines"], s["merges"], s["cost"]
    print(f"{s['photos']} photos")
    print(f"Belongings: {it['on_schedule']} of {it['real']} on the schedule ({pct(it['recall'])})")
    if it["missing_from_claim"]:
        print(f"  missing from claim: {', '.join(it['missing_from_claim'])}")
    print(f"Sightings: {sg['found']} of {sg['real']} found ({pct(sg['recall'])}); "
          f"{sg['missed_by_sam']} missed by SAM, {sg['wrongly_rejected']} wrongly rejected")
    print(f"  precision {pct(sg['precision'])}: {sg['false_finds']} false finds kept, "
          f"{sg['rightly_rejected']} caught by verification")
    print(f"Schedule lines: {ln['total']} total, {ln['false']} false, {ln['duplicates']} duplicates")
    print(f"Merges across photos: {mg['correct']} correct, {mg['wrong']} wrong, {mg['missed']} missed")
    print("By category (found / missed / false):")
    for cat, n in s["by_category"].items():
        print(f"  {cat:15s} {n['found']:3d} {n['missed']:3d} {n['false']:3d}")
    if c["total"] is not None:
        print(f"API cost: ${c['total']:.2f} for {c['photos_logged']} logged photos "
              f"(${c['per_photo']:.3f} per photo, re-runs included); calls {c['calls']}")
    else:
        print(f"API cost: not available from {USAGE_LOG.name}; calls {c['calls']}")
    print(f"Saved {out / 'evaluation.json'}")


if __name__ == "__main__":
    main()
