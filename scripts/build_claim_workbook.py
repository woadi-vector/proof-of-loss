"""Build the contents claim workbook from the line list, evidence and listings.

  python scripts/build_claim_workbook.py claims/<claim>/lines.xlsx \
      --photos claims/<claim>/photo_report.csv --docs claims/<claim>/documents.csv \
      --docs-dir claims/<claim>/files --comps claims/<claim>/comps.csv \
      --stated 76972 --out claims/<claim>/out

Writes <out>/claim_workbook.xlsx (Summary, Inventory, Documents) and CSV
copies. Inputs are read only. Real claims live under claims/, which is never
committed.

comps.csv columns: line, source, url, price, currency, region, title, retrieved.
Collect listings by hand during the job; the tool averages them, sets aside
outliers and listings from another currency or market, and cites every one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pol.claim_io import load_claim  # noqa: E402
from pol.evidence import EvidenceRegister, load_documents, load_photo_report  # noqa: E402
from pol.export import build_rows, load_comps, write_csv, write_xlsx  # noqa: E402
from pol.support import HIGH_VALUE, claim_support  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", type=Path)
    ap.add_argument("--photos", type=Path)
    ap.add_argument("--docs", type=Path)
    ap.add_argument("--docs-dir", type=Path, help="folder holding the document files, to hash them")
    ap.add_argument("--comps", type=Path)
    ap.add_argument("--stated", type=float)
    ap.add_argument("--currency", default="CAD")
    ap.add_argument("--region", default="CA")
    ap.add_argument("--high-value", type=float, default=HIGH_VALUE,
                    help="per-unit amount at which ownership needs a document, not a photo")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    claim = load_claim(a.lines, stated_total=a.stated, currency=a.currency)
    reg = EvidenceRegister()
    if a.photos:
        load_photo_report(a.photos, reg)
    if a.docs:
        load_documents(a.docs, a.docs_dir, reg)
    support = claim_support([il.line for il in claim.lines], reg, a.high_value)
    comps = load_comps(a.comps) if a.comps else {}
    wb = build_rows(claim, reg, support, comps, a.currency, a.region)
    x = write_xlsx(wb, a.out / "claim_workbook.xlsx", a.currency)
    write_csv(wb, a.out)

    print(claim.summary())
    for row in wb.summary[10:14]:
        print(f"  {row[0]}: {row[1]}")
    if reg.issues:
        print(f"evidence issues: {len(reg.issues)} (first: {reg.issues[0].ref}: {reg.issues[0].message})")
    print(f"written: {x}")


if __name__ == "__main__":
    main()
