"""Evidence register and support status on the synthetic claim."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from pol.claim_io import load_claim
from pol.evidence import EvidenceRegister, load_documents, load_photo_report
from pol.records import ScheduleLine
from pol.support import PARTIAL, SUPPORTED, UNSUPPORTED, claim_support, line_support, support_totals

FIX = Path(__file__).parent / "fixtures" / "synthetic_flood_claim"


def claim():
    res = load_claim(FIX / "lines.csv", as_of=date(2026, 11, 15))
    lines = [ln.line for ln in res.lines]
    acquired = {ln.line.line_id: ln.acquired for ln in res.lines}
    reg = load_photo_report(FIX / "photo_report.csv")
    load_documents(FIX / "documents.csv", register=reg)
    return lines, acquired, reg


def test_register_reads_both_sources():
    _, _, reg = claim()
    assert len(reg.photos) == 58 and len(reg.documents) == 13
    gift = next(d for d in reg.documents if d.doc_type == "gift_letter")
    assert gift.proves == {"ownership"}


def test_luxury_lines_by_paperwork():
    lines, acquired, reg = claim()
    e = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    by = {r.line_id: r for r in claim_support(lines, reg)}
    lux = [str(n) for n in e["luxuryLines"]]
    by_amount = {ln.line_id: ln.claimed_amount for ln in lines}
    assert sum(1 for lid in lux if by[lid].high_value) == 10, "the 690 pouch sits under the 1,000 threshold"
    for lid in lux:
        r = by[lid]
        assert r.high_value == (by_amount[lid] >= 1000)
        docs = [d for d in reg.documents if lid in d.lines]
        if acquired[lid] == "gift":
            assert r.status == PARTIAL, "a gift letter proves ownership, not value"
            if r.high_value:
                assert "giver's purchase record" in r.reason
        elif any(d.doc_type == "card_statement" for d in docs):
            assert r.status == SUPPORTED, "a card statement proves both"
        else:
            assert r.status == UNSUPPORTED, "no paperwork, and photos do not count on a high-value line"


def test_branded_photo_alone_does_not_support_a_high_value_line():
    lines, _, reg = claim()
    by = {r.line_id: r for r in claim_support(lines, reg)}
    hit = [r for r in by.values() if r.high_value and r.presence and not r.ownership]
    assert hit, "the fixture has high-value lines seen in photos with no paperwork"
    for r in hit:
        assert r.status == UNSUPPORTED
        assert "photos do not count toward ownership" in r.reason


def test_everyday_line_with_photo_is_partial_and_with_receipt_supported():
    lines, _, reg = claim()
    by = {r.line_id: r for r in claim_support(lines, reg)}
    photo_only = [r for r in by.values() if not r.high_value and r.presence and not r.value]
    assert photo_only and all(r.status == PARTIAL for r in photo_only)
    receipted = [d.lines[0] for d in reg.documents if d.doc_type == "receipt"]
    assert receipted and all(by[lid].status == SUPPORTED for lid in receipted)


def test_threshold_is_a_setting():
    reg = EvidenceRegister()
    reg.photos.append(__import__("pol.evidence", fromlist=["PhotoEvidence"]).PhotoEvidence(
        "Report", "7", lines=["L1"]))
    line = ScheduleLine("L1", "Den", "Bag", "", "", 1, 2023, 1500.0)
    assert line_support(line, reg).status == UNSUPPORTED
    assert line_support(line, reg, high_value=5000).status == PARTIAL


def test_totals_and_bad_references(tmp_path):
    lines, _, reg = claim()
    results = claim_support(lines, reg)
    t = support_totals(lines, results)
    assert sum(v["lines"] for v in t.values()) == 94
    p = tmp_path / "docs.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "file", "type", "proves", "lines"])
        w.writerow(["X1", "missing.pdf", "gift_letter", "ownership,value", "999"])
        w.writerow(["X2", "mystery.pdf", "napkin", "", ""])
    reg2 = load_documents(p, files_dir=tmp_path)
    claim_support(lines, reg2)
    msgs = " | ".join(i.message for i in reg2.issues)
    assert "not in the claim" in msgs and "not found" in msgs
    assert "gift letter is listed as proving value" in msgs
    assert "not known" in msgs and "supports no line yet" in msgs


def test_files_on_hand_are_hashed_and_untouched(tmp_path):
    f = tmp_path / "receipt_1.pdf"
    f.write_bytes(b"%PDF-1.4 synthetic")
    before = f.read_bytes()
    p = tmp_path / "docs.csv"
    p.write_text("doc_id,file,type,proves,lines\nD1,receipt_1.pdf,receipt,,5\n", encoding="utf-8")
    reg = load_documents(p, files_dir=tmp_path)
    assert len(reg.documents[0].sha256) == 64 and reg.documents[0].proves == {"ownership", "value"}
    assert f.read_bytes() == before
