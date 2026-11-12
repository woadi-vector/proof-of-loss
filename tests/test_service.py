"""The cloud check end to end, through plain JSON, as the Cloud Function calls it."""

import json

import pytest

from pol.service import InputError, check_claim


def comp(price, n=1):
    return {"source": f"Store {n}", "url": f"https://example.com/{n}", "price": price}


def base_payload():
    return {
        "loss_type": "fire",
        "as_of": "2026-11-11",
        "rooms": [{"name": "Living room"}, {"name": "Bedroom", "damage": "burned_through"},
                  {"name": "Garage", "walked": False}],
        "schedule": [
            {"line_id": "L1", "room": "Living room", "description": "65 inch TV",
             "category": "television", "brand_model": "Samsung QN65", "purchase_year": 2022,
             "claimed_amount": 1800},
            {"line_id": "L2", "room": "Living room", "description": "Soundbar",
             "category": "electronics", "brand_model": "Sonos Arc", "purchase_year": 2023,
             "claimed_amount": 900},
            {"line_id": "L3", "room": "Bedroom", "description": "Dresser", "category": "furniture",
             "purchase_year": 2015, "claimed_amount": 700},
            {"line_id": "L4", "room": "Garage", "description": "Table saw", "claimed_amount": 600},
        ],
        "sightings": [
            {"sighting_id": "S1", "room": "Living room", "description": "TV on wall",
             "line_id": "L1", "brand_model": "Samsung QN65B", "timestamp_s": 42},
            {"sighting_id": "S2", "room": "Living room", "description": "Soundbar",
             "line_id": "L2", "brand_model": "Vizio V21"},
            {"sighting_id": "S3", "room": "Living room", "description": "Floor lamp"},
        ],
        "comps": {"L1": [comp(1100, 1), comp(1200, 2), comp(1150, 3)]},
    }


def by_line(out):
    return {r["line_id"]: r for r in out["results"] if r["line_id"]}


def test_round_trips_through_json():
    out = check_claim(json.loads(json.dumps(base_payload())))
    json.dumps(out)  # everything returned is JSON-ready
    assert all(r["review_status"] == "NEEDS_REVIEW" for r in out["results"])


def test_statuses_follow_the_rules():
    out = check_claim(base_payload())
    r = by_line(out)
    assert r["L1"]["status"] == "consistent"           # QN65 vs QN65B is a variant
    assert r["L2"]["status"] == "cant_tell"            # brand differs, no second look run
    assert r["L2"]["second_look"] == "second look not run"
    assert r["L3"]["status"] == "cant_tell"            # burn-through absence
    assert r["L4"]["status"] == "not_checked"          # garage never walked
    unclaimed = [x for x in out["results"] if x["status"] == "observed_not_claimed"]
    assert [x["sighting_id"] for x in unclaimed] == ["S3"]


def test_device_second_look_confirms_or_holds():
    p = base_payload()
    p["second_looks"] = {"L2": False}
    assert by_line(check_claim(p))["L2"]["status"] == "discrepancy"
    p["second_looks"] = {"L2": None}
    assert by_line(check_claim(p))["L2"]["status"] == "cant_tell"
    p["second_looks"] = {"L2": True}
    assert by_line(check_claim(p))["L2"]["status"] == "cant_tell"


def test_burn_through_needs_a_recorded_sign():
    p = base_payload()
    p["second_looks"] = {"L3": False}
    p["absence_signs"] = {"L3": "clean floor outline, no hardware or drawer pulls in debris"}
    r = by_line(check_claim(p))["L3"]
    assert r["status"] == "not_observed"
    assert "drawer pulls" in r["reason"]


def test_price_comes_from_cited_comps_and_depreciation_is_explained():
    out = check_claim(base_payload())
    v = out["valuations"]["L1"]
    assert v["replacement_cost"] == 1150.0
    assert v["gap"] == 650.0
    assert v["age_years"] == 4.0
    assert "Straight-line" in v["depreciation_explained"]
    assert out["valuations"]["L2"]["replacement_cost"] is None
    assert any("replacement cost needed" in f for f in out["valuations"]["L2"]["flags"])


def test_documented_value_counts_only_consistent_or_accepted():
    out = check_claim(base_payload())
    dv = out["documented_value"]
    assert dv["linesCounted"] == 1 and dv["assetValue"] == 1150.0
    p = base_payload()
    p["accepted"] = ["L2"]
    p["receipts"] = {"L2": 899}
    dv = check_claim(p)["documented_value"]
    assert dv["linesCounted"] == 2 and dv["assetValue"] == 2049.0


def test_like_kind_and_quality_turns_consistent_into_discrepancy():
    p = base_payload()
    p["observed_comps"] = {"L1": [comp(600, 1), comp(650, 2)]}
    out = check_claim(p)
    r = by_line(out)["L1"]
    assert r["status"] == "discrepancy"
    assert any("like kind and quality" in d for d in r["differences"])
    f = out["like_kind_and_quality"][0]
    assert f["exceeds"] and f["betterment_total"] == 1175.0


def test_small_betterment_is_not_raised():
    p = base_payload()
    p["observed_comps"] = {"L1": [comp(1700, 1), comp(1750, 2)]}
    assert by_line(check_claim(p))["L1"]["status"] == "consistent"


def test_theft_absence_carries_no_weight():
    p = base_payload()
    p["loss_type"] = "theft"
    p["sightings"] = []
    r = by_line(check_claim(p))
    assert r["L2"]["status"] == "not_observed"
    assert r["L2"]["needs_attention"] is False


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(schedule=[]), "schedule"),
    (lambda p: p.update(loss_type="earthquake"), "loss_type"),
    (lambda p: p["schedule"][0].update(score=0.9), "unknown field"),
    (lambda p: p["schedule"].append(dict(p["schedule"][0])), "duplicate line_id"),
    (lambda p: p["rooms"][0].update(damage="smoky"), r"rooms\[0\]\.damage"),
    (lambda p: p.update(second_looks={"L2": "maybe"}), "second_looks"),
    (lambda p: p.update(observed_comps={"L9": []}), "no schedule line"),
    (lambda p: p.update(as_of="11/11/2026"), "as_of"),
])
def test_bad_input_says_what_is_wrong(mutate, message):
    p = base_payload()
    mutate(p)
    with pytest.raises(InputError, match=message):
        check_claim(p)
