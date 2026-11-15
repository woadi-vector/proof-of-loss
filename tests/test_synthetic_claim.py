"""The synthetic claim keeps the shape the tools are built against."""

from __future__ import annotations

import csv
import json
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "synthetic_flood_claim"


def rows(name):
    with open(FIX / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_shape_matches_expected():
    e = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    lines = rows("lines.csv")
    assert len(lines) == e["lines"] == 94
    total = sum(int(r["qty"] or 1) * int(r["claimed_unit_cad"]) for r in lines)
    assert total == e["lineSumCad"]
    assert e["statedTotalCad"] == total - 437, "stated total is deliberately off"
    assert 0.4 <= e["luxurySumCad"] / total <= 0.6, "luxury lines are about half the claim"
    assert len(e["luxuryLines"]) == 11


def test_built_in_gaps_are_present():
    e = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    by_line = {int(r["line"]): r for r in rows("lines.csv")}
    assert by_line[e["missingQtyLine"]]["qty"] == ""
    assert by_line[e["missingRoomLine"]]["room"] == ""


def test_evidence_points_at_real_lines():
    known = {r["line"] for r in rows("lines.csv")}
    for r in rows("photo_report.csv"):
        assert r["lines"] == "" or r["lines"] in known
    for r in rows("documents.csv"):
        assert r["lines"] in known


def test_three_luxury_lines_have_no_photo():
    e = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    photographed = {r["lines"] for r in rows("photo_report.csv")}
    assert all(str(n) not in photographed for n in e["luxuryWithNoPhoto"])
    assert len(e["luxuryWithNoPhoto"]) == 3


def test_gift_letters_prove_ownership_only():
    for r in rows("documents.csv"):
        if r["type"] == "gift_letter":
            assert r["proves"] == "ownership"


def test_claims_folder_is_gitignored():
    ignore = (Path(__file__).parent.parent / ".gitignore").read_text(encoding="utf-8").split()
    assert "claims/" in ignore, "real client files must never be committed"
