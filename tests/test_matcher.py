"""Claimed versus observed: every status and every loss-type rule, on synthetic claims."""

from __future__ import annotations

from datetime import date

from pol.matcher import brand_model_difference, match_schedule
from pol.records import LossType, Room, RoomDamage, ScheduleLine, Sighting, Status


class Stub:
    """A second look that answers from a fixed table."""

    def __init__(self, matches=None, seen=None):
        self.matches, self.seen, self.calls = matches or {}, seen or {}, []

    def matches_claim(self, line, sightings):
        self.calls.append(("matches", line.line_id))
        return self.matches.get(line.line_id)

    def seen_in_room(self, line, room):
        self.calls.append(("seen", line.line_id))
        return self.seen.get(line.line_id)


def tv(**kw):
    base = dict(line_id="L1", room="Living room", description="65 inch TV", category="television",
                brand_model="Samsung QN65", quantity=1, purchase_year=2022, claimed_amount=1400.0)
    base.update(kw)
    return ScheduleLine(**base)


def seen(line_id="L1", **kw):
    base = dict(sighting_id="S1", room="Living room", description="TV on wall", line_id=line_id,
                brand_model="Samsung QN65", quantity=1)
    base.update(kw)
    return Sighting(**base)


ROOMS = [Room("Living room"), Room("Bedroom 2", walked=False)]


def one(results, line_id="L1"):
    return next(r for r in results if r.line_id == line_id)


def test_consistent():
    r = one(match_schedule([tv()], [seen()], ROOMS, LossType.WATER))
    assert r.status is Status.CONSISTENT and not r.needs_attention
    assert r.review_status == "NEEDS_REVIEW", "nothing is signed off automatically"


def test_brand_difference_confirmed_is_discrepancy():
    stub = Stub(matches={"L1": False})
    r = one(match_schedule([tv()], [seen(brand_model="TCL 65S")], ROOMS, LossType.WATER, stub))
    assert r.status is Status.DISCREPANCY
    assert any("brand" in d for d in r.differences)
    assert ("matches", "L1") in stub.calls


def test_brand_difference_without_second_look_is_cant_tell():
    r = one(match_schedule([tv()], [seen(brand_model="TCL 65S")], ROOMS, LossType.WATER))
    assert r.status is Status.CANT_TELL
    assert r.second_look == "second look not run"


def test_second_look_disagrees_is_cant_tell():
    stub = Stub(matches={"L1": True})
    r = one(match_schedule([tv()], [seen(brand_model="TCL 65S")], ROOMS, LossType.WATER, stub))
    assert r.status is Status.CANT_TELL


def test_unknown_brand_on_walk_is_not_a_difference():
    assert brand_model_difference("Samsung QN65", "") is None
    assert brand_model_difference("Samsung QN65", "samsung") is None
    assert brand_model_difference("Samsung QN65", "Samsung QN65B") is None, "a suffix variant is the same model"
    assert brand_model_difference("Samsung QN65", "Samsung UN55") is not None
    r = one(match_schedule([tv()], [seen(brand_model="")], ROOMS, LossType.WATER))
    assert r.status is Status.CONSISTENT


def test_photo_predates_purchase_year_needs_no_second_look():
    line = tv(photo_dates=[date(2019, 7, 4)])
    stub = Stub()
    r = one(match_schedule([line], [seen()], ROOMS, LossType.WATER, stub))
    assert r.status is Status.DISCREPANCY
    assert "predates stated purchase year 2022" in r.differences[0]
    assert stub.calls == [], "a document fact is not a visual judgement"


def test_unclear_frame_is_cant_tell():
    r = one(match_schedule([tv()], [seen(clear=False)], ROOMS, LossType.WATER))
    assert r.status is Status.CANT_TELL


def test_quantity_short_counts_only_where_countable():
    chairs = ScheduleLine("L2", "Living room", "Dining chairs", "chair", "", quantity=6)
    sight = Sighting("S2", "Living room", "chairs", line_id="L2", quantity=4)
    stub = Stub(matches={"L2": False})
    water = one(match_schedule([chairs], [sight], ROOMS, LossType.WATER, stub), "L2")
    assert water.status is Status.DISCREPANCY and "claimed 6, counted 4" in water.differences[0]
    burned = [Room("Living room", damage=RoomDamage.BURNED_THROUGH)]
    fire = one(match_schedule([chairs], [sight], burned, LossType.FIRE, stub), "L2")
    assert fire.status is Status.CONSISTENT, "some chairs may have burned; a short count is not a flag"


def test_room_not_walked_is_not_checked():
    line = tv(line_id="L3", room="Bedroom 2")
    r = one(match_schedule([line], [], ROOMS, LossType.WATER, Stub(seen={"L3": False})), "L3")
    assert r.status is Status.NOT_CHECKED and not r.needs_attention


def test_room_missing_from_walk_is_not_checked():
    line = tv(line_id="L4", room="Garage")
    r = one(match_schedule([line], [], ROOMS, LossType.WATER), "L4")
    assert r.status is Status.NOT_CHECKED


def test_not_observed_needs_confirmed_second_look():
    stub = Stub(seen={"L1": False})
    r = one(match_schedule([tv()], [], ROOMS, LossType.WATER, stub))
    assert r.status is Status.NOT_OBSERVED and r.needs_attention
    r2 = one(match_schedule([tv()], [], ROOMS, LossType.WATER, Stub(seen={"L1": None})))
    assert r2.status is Status.CANT_TELL
    r3 = one(match_schedule([tv()], [], ROOMS, LossType.WATER))
    assert r3.status is Status.CANT_TELL


def test_burn_through_absence_is_cant_tell_without_a_sign():
    rooms = [Room("Living room", damage=RoomDamage.BURNED_THROUGH)]
    stub = Stub(seen={"L1": False})
    r = one(match_schedule([tv()], [], rooms, LossType.FIRE, stub))
    assert r.status is Status.CANT_TELL
    assert stub.calls == [], "no second look is asked when absence is expected"
    signed = one(match_schedule([tv()], [], rooms, LossType.FIRE, stub,
                                absence_signs={"L1": "wall mount intact, no heat shadow behind it"}))
    assert signed.status is Status.NOT_OBSERVED
    assert "wall mount intact" in signed.reason


def test_burned_remains_count_as_seen():
    rooms = [Room("Living room", damage=RoomDamage.BURNED_THROUGH)]
    r = one(match_schedule([tv()], [seen(brand_model="", remains=True)], rooms, LossType.FIRE))
    assert r.status is Status.CONSISTENT


def test_theft_absence_carries_no_weight():
    r = one(match_schedule([tv()], [], ROOMS, LossType.THEFT, Stub(seen={"L1": False})))
    assert r.status is Status.NOT_OBSERVED and not r.needs_attention


def test_observed_not_claimed():
    extra = Sighting("S9", "Living room", "Sonos soundbar", line_id=None)
    results = match_schedule([tv()], [seen(), extra], ROOMS, LossType.WATER)
    r = next(r for r in results if r.sighting_id == "S9")
    assert r.status is Status.OBSERVED_NOT_CLAIMED and not r.needs_attention


def test_clean_claim_raises_no_flags():
    """The clean control: an honest schedule fully seen on the walk gets zero flags."""
    schedule = [tv(), ScheduleLine("L2", "Living room", "Grey sofa", "couch", "Ashley", 1, 2020, 900.0)]
    sightings = [seen(), Sighting("S2", "Living room", "grey sofa", line_id="L2", brand_model="Ashley")]
    results = match_schedule(schedule, sightings, ROOMS, LossType.WATER, Stub())
    assert all(r.status is Status.CONSISTENT for r in results)
    assert not any(r.needs_attention for r in results)


def test_room_names_match_loosely():
    r = one(match_schedule([tv(room="living  ROOM")], [], [Room("Living Room", walked=False)], LossType.WATER))
    assert r.status is Status.NOT_CHECKED
