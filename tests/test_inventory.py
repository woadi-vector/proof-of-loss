"""Document mode on the first walk, then check mode when the sworn schedule arrives."""

from __future__ import annotations

from datetime import date

from pol.inventory import Observation, baseline_assignment, build_inventory, to_sightings, totals
from pol.matcher import match_schedule
from pol.pricing import Comparable
from pol.records import Condition, LossType, Room, ScheduleLine, Status

AS_OF = date(2026, 11, 9)


def comps(*prices):
    return [Comparable(source=f"Store {i}", url=f"https://example.com/{i}", price=p) for i, p in enumerate(prices)]


def first_walk():
    return [
        Observation("o1", "Living room", "65 inch TV, screen cracked", "television", "Samsung QN65",
                    condition=Condition.DAMAGED, stated_age_years=3, frame="f1.jpg",
                    quote="Samsung sixty-five, screen cracked, maybe three years old"),
        Observation("o2", "Living room", "Grey sectional, water line at base", "couch", "",
                    condition=Condition.DAMAGED, stated_age_years=5, frame="f2.jpg"),
        Observation("o3", "Living room", "Floor lamp, dry", "lamp", "", condition=Condition.UNDAMAGED,
                    frame="f3.jpg"),
        Observation("o4", "Kitchen", "Mention with no frame", "table", frame=""),
    ]


def test_document_mode_records_and_values():
    recs = build_inventory(first_walk(), comps={"o1": comps(950, 1000, 1050)}, as_of=AS_OF)
    assert [r.observation.observation_id for r in recs] == ["o1", "o2", "o3"], "no frame, no record"
    tv = recs[0]
    assert tv.valuation.replacement_cost == 1000.0
    assert tv.valuation.age_years == 3.0
    assert "Estimated 3 years old by the adjuster" in tv.valuation.depreciation_explained
    assert "age is the adjuster's estimate on the walk" in tv.flags
    assert tv.valuation.claimed_total is None, "nothing is claimed yet in document mode"
    sofa = recs[1]
    assert sofa.valuation.replacement_cost is None and any("replacement cost needed" in f for f in sofa.flags)
    t = totals(recs)
    assert t["records"] == 3 and t["replacementCost"] == 1000.0 and t["unpriced"] == 2
    assert t["byCondition"] == {"damaged": 2, "undamaged": 1}


def test_baseline_pairs_only_one_to_one():
    recs = build_inventory(first_walk(), as_of=AS_OF)
    schedule = [
        ScheduleLine("L1", "Living room", "TV", "television", "Samsung QN65", 1, 2023, 1400.0),
        ScheduleLine("L2", "Living room", "Sectional", "couch", "", 1, 2021, 2500.0),
        ScheduleLine("L3", "Living room", "Lamp A", "lamp", "", 1, 2022, 150.0),
        ScheduleLine("L4", "Living room", "Lamp B", "lamp", "", 1, 2022, 150.0),
    ]
    a = baseline_assignment(schedule, recs)
    assert a == {"rec-001": "L1", "rec-002": "L2"}, "two lamp lines, one lamp record: left for a person"


def test_check_mode_after_first_walk():
    recs = build_inventory(first_walk(), as_of=AS_OF)
    schedule = [
        ScheduleLine("L1", "Living room", "TV", "television", "Samsung QN65", 1, 2023, 1400.0),
        ScheduleLine("L2", "Living room", "Sectional", "couch", "", 1, 2021, 2500.0),
        ScheduleLine("L3", "Living room", "Floor lamp", "lamp", "", 1, 2022, 150.0),
        ScheduleLine("L5", "Living room", "Gaming PC", "computer", "", 1, 2025, 3200.0),
    ]
    sightings = to_sightings(recs, baseline_assignment(schedule, recs))

    class Stub:
        def matches_claim(self, line, s):
            return False   # confirms the visual difference

        def seen_in_room(self, line, room):
            return False   # confirms the absence

    results = match_schedule(schedule, sightings, [Room("Living room")], LossType.WATER, Stub())
    by = {r.line_id: r for r in results if r.line_id}
    assert by["L1"].status is Status.CONSISTENT
    assert by["L2"].status is Status.CONSISTENT
    assert by["L3"].status is Status.DISCREPANCY, "the lamp was recorded undamaged on the first walk"
    assert "recorded undamaged" in by["L3"].differences[0]
    assert by["L5"].status is Status.NOT_OBSERVED, "claimed, in a walked room, never recorded"
    assert not [r for r in results if r.status is Status.OBSERVED_NOT_CLAIMED]


def test_undamaged_flag_needs_second_look():
    recs = build_inventory(first_walk(), as_of=AS_OF)
    schedule = [ScheduleLine("L3", "Living room", "Floor lamp", "lamp", "", 1, 2022, 150.0)]
    sightings = to_sightings(recs, baseline_assignment(schedule, recs))
    r = next(x for x in match_schedule(schedule, sightings, [Room("Living room")], LossType.WATER)
             if x.line_id == "L3")
    assert r.status is Status.CANT_TELL, "without a confirming second look it is held, not flagged"
