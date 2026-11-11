"""Process one recorded walk into first-walk records.

  python scripts/walk.py <video> --script <notes.json>        # stub answers, no API
  python scripts/walk.py <video> --provider google             # once Google is attached

Writes to runs/walk_<stamp>/: frames/, audio.wav, manifest.json (every file
hash), ledger.json (every model call and its cost), and records.json (the
document-mode records with value and depreciation). Answers are cached under
runs/cache/, so a second run of the same walk costs $0.

The --script file is JSON shaped like:
  {"segments": [{"start_s": 0, "end_s": 2, "text": "Living room, smoke damage."}],
   "rooms": [{"name": "Living room", "start_s": 0, "damage": "light"}],
   "mentions": [{"mention_id": "m1", "room": "Living room", "description": "65 inch TV",
                 "at_s": 2.5, "category": "television", "brand_model": "Samsung QN65",
                 "condition": "damaged", "stated_age_years": 3, "quote": "..."}],
   "frames": {"m1": {"clear": true, "shows_item": true, "note": ""}}}
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pol.inventory import build_inventory, totals  # noqa: E402
from pol.providers import (CachedProvider, FrameCheck, GoogleProvider, Segment,  # noqa: E402
                           StubProvider, _notes_from_json)
from pol.walk import process_walk  # noqa: E402


def stub_from(path: Path) -> StubProvider:
    d = json.loads(path.read_text(encoding="utf-8"))
    segments = [Segment(**s) for s in d.get("segments", [])]
    notes = _notes_from_json({"rooms": [r | {"damage": r.get("damage", "none")} for r in d.get("rooms", [])],
                              "mentions": [m | {"condition": m.get("condition", "unknown")}
                                           for m in d.get("mentions", [])]})
    frames = {k: FrameCheck(**v) for k, v in d.get("frames", {}).items()}
    return StubProvider(segments, notes, frames)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--provider", choices=["stub", "google"], default="stub")
    ap.add_argument("--script", type=Path, help="JSON answers for the stub provider")
    ap.add_argument("--out", type=Path, help="output folder (default runs/walk_<stamp>)")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    if not a.video.exists():
        sys.exit(f"No video at {a.video}")
    if a.provider == "stub":
        if not a.script:
            sys.exit("--provider stub needs --script <notes.json>")
        provider = stub_from(a.script)
    else:
        provider = GoogleProvider()
    if not a.no_cache:
        provider = CachedProvider(provider, ROOT / "runs" / "cache")

    out = a.out or ROOT / "runs" / f"walk_{datetime.now():%Y%m%d_%H%M%S}"
    res = process_walk(a.video, provider, out)
    recs = build_inventory(res.observations)
    rows = [{"record": r.record_id, "room": r.room, "item": r.observation.description,
             "condition": r.observation.condition.value, "frame": r.observation.frame,
             "atS": r.observation.timestamp_s, "clear": r.observation.clear,
             "replacementCost": r.valuation.replacement_cost,
             "actualCashValue": r.valuation.actual_cash_value,
             "explained": r.valuation.depreciation_explained, "flags": r.flags} for r in recs]
    (out / "records.json").write_text(json.dumps({"records": rows, "totals": totals(recs)}, indent=2),
                                      encoding="utf-8")

    s = res.ledger.summary()
    print(f"walk: {res.manifest['video']['name']} ({res.manifest['video']['durationS']} s), "
          f"sha256 {res.manifest['video']['sha256'][:12]}")
    print(f"rooms: {', '.join(r.name for r in res.rooms) or 'none named'}")
    print(f"records: {len(recs)} ({sum(1 for r in recs if not r.observation.clear)} need a closer look)")
    print(f"model calls: {s['calls']} ({s['liveCalls']} live, {s['cachedCalls']} cached), cost ${s['costUsd']:.4f}")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
