"""Importer on the synthetic claim and on messier hand-made lists."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from pol.claim_io import load_claim

FIX = Path(__file__).parent / "fixtures" / "synthetic_flood_claim"
AS_OF = date(2026, 11, 14)


def expected():
    return json.loads((FIX / "expected.json").read_text(encoding="utf-8"))


def test_synthetic_claim_reads_and_flags():
    e = expected()
    res = load_claim(FIX / "lines.csv", stated_total=e["statedTotalCad"], as_of=AS_OF)
    assert len(res.lines) == 94
    assert res.computed_total == e["lineSumCad"]
    assert res.gap == 437.0, "lines add up to more than the stated total"
    assert "off by CAD 437.00 (under the lines)" in res.summary()
    flagged = {(i.line_id, i.field) for i in res.issues}
    assert (str(e["missingQtyLine"]), "qty") in flagged
    assert (str(e["missingRoomLine"]), "room") in flagged
    assert res.columns["unit_price"] == "claimed_unit_cad"


def test_gift_and_brand_model_carried():
    res = load_claim(FIX / "lines.csv", as_of=AS_OF)
    gifts = [ln for ln in res.lines if ln.acquired == "gift"]
    assert gifts, "the acquisition column is kept"
    assert all(ln.line.brand_model for ln in gifts)


def test_source_file_not_modified():
    p = FIX / "lines.csv"
    before = p.read_bytes()
    load_claim(p, as_of=AS_OF)
    assert p.read_bytes() == before


def test_messy_csv_aliases_total_row_and_line_totals(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text(
        "Item #,Location,Description,Make,Qty,Age,Line Total\n"
        "1,Basement,Sofa,Brookline,1,4,\"$1,200.00\"\n"
        "2,Basement,Dining chairs,Norbeck,4,6,800\n"
        "3,,Lamp,,,2,90\n"
        "4,Office,Monitor,Quorra,2,1,\n"
        ",,TOTAL,,,,\"2,000\"\n", encoding="utf-8")
    res = load_claim(p, as_of=AS_OF)
    assert [ln.line.line_id for ln in res.lines] == ["1", "2", "3", "4"]
    assert res.stated_total == 2000.0, "the total row is read as the stated total, not a line"
    assert res.computed_total == 2090.0
    chairs = res.lines[1].line
    assert chairs.claimed_amount == 200.0 and chairs.purchase_year == 2020
    issues = {(i.line_id, i.field) for i in res.issues}
    assert ("2", "price") in issues, "a unit price worked out from a total is flagged"
    assert ("3", "qty") in issues and ("3", "room") in issues and ("3", "brand") in issues
    assert ("4", "price") in issues and res.unpriced_lines == 1


def test_xlsx_and_duplicate_line_numbers(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Line", "Room", "Item", "Brand", "Model", "Quantity", "Year Purchased", "Unit Price"])
    ws.append([1, "Den", "TV", "Varion", "VX55Q", 1, 2022, 799])
    ws.append([1, "Den", "Soundbar", "Varion", "VS-300", 1, 2022, 249])
    p = tmp_path / "list.xlsx"
    wb.save(p)
    res = load_claim(p, as_of=AS_OF)
    assert res.computed_total == 1048.0
    assert ("1", "line") in {(i.line_id, i.field) for i in res.issues}


def test_unit_times_qty_disagreeing_with_total(tmp_path):
    p = tmp_path / "list.csv"
    p.write_text("Line,Item,Qty,Unit Price,Total\n1,Chairs,4,100,450\n", encoding="utf-8")
    res = load_claim(p, as_of=AS_OF)
    assert any("does not match the line total" in i.message for i in res.issues)
