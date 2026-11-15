"""Check a claimed line list before any pricing work.

  python scripts/import_claim.py claims/<claim>/lines.xlsx --stated 76972
  python scripts/import_claim.py tests/fixtures/synthetic_flood_claim/lines.csv --stated 73213

Prints the line count, what the lines add up to against the stated total,
which columns were read, and every issue (missing quantity, room, brand,
model, age or price; duplicate line numbers; unit price that does not match
the line total). Reads the file only; writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pol.claim_io import load_claim  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path)
    ap.add_argument("--stated", type=float, help="the claim total as the policyholder stated it")
    ap.add_argument("--currency", default="CAD")
    a = ap.parse_args()
    if not a.path.exists():
        sys.exit(f"No file at {a.path}")

    res = load_claim(a.path, stated_total=a.stated, currency=a.currency)
    print(res.summary())
    print("columns read: " + ", ".join(f"{k} <- {v!r}" for k, v in res.columns.items()))
    if res.extra_headers:
        print("columns not used: " + ", ".join(res.extra_headers))
    by_field: dict[str, list[str]] = {}
    for i in res.issues:
        by_field.setdefault(i.field, []).append(i.line_id or "file")
    for fld, ids in sorted(by_field.items()):
        print(f"  {fld}: {len(ids)} ({', '.join(ids[:12])}{' ...' if len(ids) > 12 else ''})")


if __name__ == "__main__":
    main()
