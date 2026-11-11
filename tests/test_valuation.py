"""Pricing, depreciation and the OVER_VALUE feed, with hand-checked numbers."""

from __future__ import annotations

from datetime import date

from pol.matcher import match_schedule
from pol.pricing import Comparable
from pol.records import LossType, Room, ScheduleLine, Sighting, Status
from pol.valuation import documented_value, value_line

AS_OF = date(2026, 11, 9)


def comps(*prices):
    return [Comparable(source=f"Store {i}", url=f"https://example.com/{i}", price=p) for i, p in enumerate(prices)]


def test_comps_average_and_depreciation():
    line = ScheduleLine("L1", "Living room", "65 inch TV", "television", "Samsung QN65",
                        quantity=1, purchase_year=2022, claimed_amount=1400.0)
    v = value_line(line, comps(899.99, 949.0, 1000.0), as_of=AS_OF)
    assert v.replacement_cost == 949.66                       # (899.99 + 949 + 1000) / 3
    assert v.age_years == 4.0 and v.useful_life_years == 7
    assert v.depreciation_pct == round(4 / 7 * 100, 1)        # 57.1
    assert v.actual_cash_value == round(949.66 * (1 - 4 / 7), 2)
    assert v.recoverable_depreciation == round(949.66 - v.actual_cash_value, 2)
    assert v.gap == round(1400.0 - 949.66, 2), "claimed minus documented, shown as a number"
    assert "Bought 2022" in v.depreciation_explained


def test_outlier_comp_set_aside():
    line = ScheduleLine("L1", "Living room", "Sofa", "couch", quantity=1, purchase_year=2024)
    v = value_line(line, comps(800, 850, 900, 5000), as_of=AS_OF)
    assert v.replacement_cost == 850.0
    assert any("far from the median" in f for f in v.flags)


def test_receipt_outranks_comps_and_cap_applies():
    line = ScheduleLine("L1", "Den", "Old lamp", "lamp", quantity=2, purchase_year=2010, claimed_amount=120.0)
    v = value_line(line, comps(10, 11, 12), receipt=95.0, as_of=AS_OF)
    assert v.replacement_cost == 95.0 and v.price_basis == "receipt"
    assert v.depreciation_pct == 80.0, "16 years of a 10-year life, capped"
    assert v.documented_total == 190.0 and v.claimed_total == 240.0 and v.gap == 50.0


def test_no_sources_no_price():
    line = ScheduleLine("L1", "Den", "Rug", "rug", purchase_year=2023)
    v = value_line(line, None, as_of=AS_OF)
    assert v.replacement_cost is None and v.actual_cash_value is None
    assert any("replacement cost needed" in f for f in v.flags)


def test_documented_value_counts_only_verified_lines():
    schedule = [
        ScheduleLine("L1", "Living room", "TV", "television", "Samsung", 1, 2022, 1400.0),
        ScheduleLine("L2", "Living room", "Sofa", "couch", "", 1, 2020, 900.0),
        ScheduleLine("L3", "Bedroom", "Dresser", "table", "", 1, 2018, 600.0),
    ]
    sightings = [Sighting("S1", "Living room", "TV", line_id="L1", brand_model="Samsung"),
                 Sighting("S2", "Living room", "sofa", line_id="L2", clear=False)]
    rooms = [Room("Living room"), Room("Bedroom", walked=False)]
    results = match_schedule(schedule, sightings, rooms, LossType.WATER)
    assert [r.status for r in results] == [Status.CONSISTENT, Status.CANT_TELL, Status.NOT_CHECKED]
    vals = {ln.line_id: value_line(ln, comps(1000, 1000), as_of=AS_OF) for ln in schedule}

    out = documented_value(results, vals)
    assert out["assetValue"] == 1000.0 and out["linesCounted"] == 1
    assert out["linesExcluded"] == {"cant_tell": 1, "not_checked": 1}

    out2 = documented_value(results, vals, accepted={"L2"})
    assert out2["assetValue"] == 2000.0, "a line a person accepted in review counts"
