"""Like kind and quality check, with hand-checked numbers."""

from __future__ import annotations

from pol.lkq import apply_lkq, like_kind_check
from pol.matcher import match_schedule
from pol.pricing import Comparable
from pol.records import LossType, Room, ScheduleLine, Sighting, Status


def comps(*prices):
    return [Comparable(source=f"Store {i}", url=f"https://example.com/{i}", price=p) for i, p in enumerate(prices)]


def claimed_tv(amount=2800.0, qty=1):
    return ScheduleLine("L1", "Living room", "75 inch OLED TV", "television", "", qty, 2023, amount)


def seen_tv(**kw):
    base = dict(sighting_id="S1", room="Living room", description="55 inch LED TV", line_id="L1",
                brand_model="Samsung UN55", timestamp_s=192)
    base.update(kw)
    return Sighting(**base)


def test_betterment_shown_in_dollars():
    f = like_kind_check(claimed_tv(), seen_tv(), comps(600, 650, 700))
    assert f.observed_replacement == 650.0
    assert f.betterment_per_unit == 2150.0 and f.betterment_total == 2150.0
    assert f.exceeds
    assert "Samsung UN55, at 3:12 in the walk" in f.explained
    assert "averages $650.00 across 3 cited listings" in f.explained
    assert "Difference $2,150.00 per unit, $2,150.00 total." in f.explained


def test_within_tolerance_is_not_raised():
    f = like_kind_check(claimed_tv(amount=780.0), seen_tv(), comps(600, 650, 700))
    assert not f.exceeds, "780 is 20% over 650, inside the 25% tolerance"
    assert "within tolerance" in f.explained


def test_dollar_floor():
    line = ScheduleLine("L2", "Den", "Lamp", "lamp", "", 1, 2023, 60.0)
    f = like_kind_check(line, seen_tv(line_id="L2", brand_model="", description="desk lamp"), comps(20, 22, 24))
    assert f.betterment_total == 38.0 and not f.exceeds, "below the $50 floor"


def test_quantity_multiplies_total():
    f = like_kind_check(claimed_tv(amount=400.0, qty=6), seen_tv(brand_model="", description="dining chair"),
                        comps(150, 160, 170))
    assert f.betterment_per_unit == 240.0 and f.betterment_total == 1440.0 and f.exceeds


def test_no_comparison_without_evidence():
    assert not like_kind_check(claimed_tv(), seen_tv(clear=False), comps(600, 650)).exceeds
    no_comps = like_kind_check(claimed_tv(), seen_tv(), [])
    assert not no_comps.exceeds and "No cited listings" in no_comps.explained
    unknown = like_kind_check(claimed_tv(), seen_tv(brand_model="", description=""), comps(600, 650))
    assert not unknown.exceeds and "not identified" in unknown.explained


def test_apply_turns_consistent_into_discrepancy():
    line = claimed_tv()
    sighting = seen_tv()
    results = match_schedule([line], [sighting], [Room("Living room")], LossType.WATER)
    assert results[0].status is Status.CONSISTENT, "the TV was there; the matcher alone sees no problem"
    apply_lkq(results, [like_kind_check(line, sighting, comps(600, 650, 700))])
    r = results[0]
    assert r.status is Status.DISCREPANCY and r.needs_attention
    assert any("Difference $2,150.00" in d for d in r.differences)
    assert r.review_status == "NEEDS_REVIEW"


def test_apply_leaves_other_statuses_but_adds_the_gap():
    line = claimed_tv()
    sighting = seen_tv(clear=False)
    results = match_schedule([line], [sighting], [Room("Living room")], LossType.WATER)
    assert results[0].status is Status.CANT_TELL
    apply_lkq(results, [like_kind_check(line, seen_tv(), comps(600, 650, 700))])
    assert results[0].status is Status.CANT_TELL
    assert any("like kind and quality" in d for d in results[0].differences)
