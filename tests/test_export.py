"""Claim workbook on the synthetic claim, with a few hand-collected listings."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from pol.claim_io import load_claim
from pol.evidence import load_documents, load_photo_report
from pol.export import DOCUMENT_COLUMNS, INVENTORY_COLUMNS, build_rows, load_comps, write_csv, write_xlsx
from pol.support import claim_support

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures" / "synthetic_flood_claim"
AS_OF = date(2026, 11, 15)


def comps_file(tmp_path, line_id):
    p = tmp_path / "comps.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "source", "url", "price", "currency", "region", "title", "retrieved"])
        w.writerow([line_id, "Store A", "https://example.com/a", "1,000", "CAD", "CA", "x", "2026-11-15"])
        w.writerow([line_id, "Store B", "https://example.com/b", "1100", "cad", "ca", "x", "2026-11-15"])
        w.writerow([line_id, "Store C", "https://example.com/c", "900", "USD", "US", "x", "2026-11-15"])
    return p


def build(tmp_path):
    e = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    claim = load_claim(FIX / "lines.csv", stated_total=e["statedTotalCad"], as_of=AS_OF)
    reg = load_photo_report(FIX / "photo_report.csv")
    load_documents(FIX / "documents.csv", register=reg)
    support = claim_support([il.line for il in claim.lines], reg)
    first = claim.lines[0].line.line_id
    comps = load_comps(comps_file(tmp_path, first))
    return e, claim, build_rows(claim, reg, support, comps, as_of=AS_OF), first


def test_rows_follow_the_layout(tmp_path):
    e, claim, wb, first = build(tmp_path)
    assert len(wb.inventory) == 94 and all(len(r) == len(INVENTORY_COLUMNS) for r in wb.inventory)
    assert len(wb.documents) == 13 + 58 and all(len(r) == len(DOCUMENT_COLUMNS) for r in wb.documents)
    row = wb.inventory[0]
    assert row[9] == 1050.0, "the USD listing is set aside; the two CAD listings average 1,050"
    assert row[10] == "https://example.com/a\nhttps://example.com/b"
    assert "another currency set aside" in row[15]
    missing_qty = next(r for r in wb.inventory if r[0] == str(e["missingQtyLine"]))
    assert "no quantity given" in missing_qty[15]
    gift = next(r for r in wb.inventory if r[15].endswith("gift"))
    assert gift[14] == "partial"


def test_summary_carries_the_gap(tmp_path):
    _, _, wb, _ = build(tmp_path)
    s = dict((r[0], r[1]) for r in wb.summary)
    assert s["Claimed, sum of lines"] == 73650.0
    assert s["Claimed, stated total"] == 73213.0
    assert s["Difference (lines minus stated)"] == 437.0
    assert s["Lines priced from listings"] == 1


def test_xlsx_and_csv_written(tmp_path):
    _, _, wb, _ = build(tmp_path)
    x = write_xlsx(wb, tmp_path / "out" / "claim_workbook.xlsx")
    paths = write_csv(wb, tmp_path / "out")
    book = load_workbook(x)
    assert book.sheetnames == ["Summary", "Inventory", "Documents"]
    inv = book["Inventory"]
    assert [c.value for c in inv[1]] == INVENTORY_COLUMNS
    assert inv.max_row == 96, "header, 94 lines, totals row"
    assert inv.cell(96, 9).value == "=SUM(I2:I95)"
    assert inv.cell(2, 15).fill.fgColor.rgb.endswith(("C6EFCE", "FFEB9C", "FFC7CE"))
    with open(paths[0], newline="", encoding="utf-8") as f:
        assert len(list(csv.reader(f))) == 95


def test_command_runs_end_to_end(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_claim_workbook.py"),
                        str(FIX / "lines.csv"), "--photos", str(FIX / "photo_report.csv"),
                        "--docs", str(FIX / "documents.csv"), "--stated", "73213", "--out", str(out)],
                       capture_output=True, text=True, check=True)
    assert "off by CAD 437.00" in r.stdout and (out / "claim_workbook.xlsx").exists()
