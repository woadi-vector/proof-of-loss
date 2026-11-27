"""Pricing walk lines from recorded Google Shopping answers: no network, no quota."""

from datetime import date

import pytest

from pol.price_service import PriceInputError, price_lines, query_for

TV_ANSWER = {"search_metadata": {"id": "t1"}, "shopping_results": [
    {"title": "TCL 75\" Class S4 4K LED Smart TV 75S451", "source": "Best Buy", "price": "$549.99",
     "extracted_price": 549.99, "link": "https://bestbuy.example/tcl75s451"},
    {"title": "TCL 75S451 75-Inch 4K Smart TV", "source": "Walmart", "price": "$529.00",
     "extracted_price": 529.0, "link": "https://walmart.example/tcl"},
    {"title": "TCL 75S451 Refurbished", "source": "Best Buy", "price": "$399.99",
     "extracted_price": 399.99, "link": "https://bestbuy.example/refurb"},
    {"title": "TCL 75S451 75 inch", "source": "eBay - seller123", "price": "$300.00",
     "extracted_price": 300.0, "link": "https://ebay.example/x"},
    {"title": "TCL 65S451 65 inch", "source": "Target", "price": "$399.99",
     "extracted_price": 399.99, "link": "https://target.example/65"},
    {"title": "Wall mount for TCL 75S451", "source": "Amazon.com", "price": "$39.99",
     "extracted_price": 39.99, "link": "https://amazon.example/mount"},
]}
STRIP_ANSWER = {"shopping_results": [
    {"title": "10-Outlet Power Strip Surge Protector", "source": "The Home Depot", "price": "$24.98",
     "extracted_price": 24.98, "link": "https://hd.example/strip"},
]}


def fake_get(params: dict) -> dict:
    return TV_ANSWER if "75S451" in params["q"] else STRIP_ANSWER


def test_query_uses_the_spoken_model_and_requires_it():
    q, must, note = query_for("television", "TCL 75S451")
    assert q == "TCL 75S451 television" and must == ["75s451"] and "model the adjuster named" in note
    q, must, note = query_for("power strip", "")
    assert q == "power strip" and must == [] and "general price" in note


def test_prices_from_kept_listings_and_shows_every_set_aside_reason():
    r = price_lines({"lines": [{"line_id": "1", "description": "television", "brand_model": "TCL 75S451"},
                               {"line_id": "2", "description": "power strip"}]},
                    fake_get, today=date(2026, 11, 22))
    tv, strip = r["lines"]
    assert [k["source"] for k in tv["kept"]] == ["Best Buy", "Walmart"]
    assert tv["replacement_cost"] == 539.5 and tv["currency"] == "USD"
    why = {s["title"]: s["why"] for s in tv["set_aside"]}
    assert why["TCL 75S451 Refurbished"].startswith("not new")
    assert "trusted list" in why["TCL 75S451 75 inch"]
    assert "lacks: 75s451" in why["TCL 65S451 65 inch"]        # a different model is not like kind
    assert "accessory" in why["Wall mount for TCL 75S451"]
    assert len(tv["answer_sha256"]) == 64
    assert strip["replacement_cost"] == 24.98 and "thin price support: one listing" in strip["flags"]
    assert r["priced"] == 2 and r["total_replacement_cost"] == 564.48


def test_a_failed_search_leaves_the_line_unpriced_and_the_rest_intact():
    def flaky(params):
        if "75S451" in params["q"]:
            return {"error": "quota"}
        return STRIP_ANSWER
    r = price_lines({"lines": [{"line_id": "1", "description": "television", "brand_model": "TCL 75S451"},
                               {"line_id": "2", "description": "power strip"}]}, flaky)
    assert r["lines"][0]["replacement_cost"] is None and "quota" in r["lines"][0]["error"]
    assert r["priced"] == 1


def test_rejects_bad_input():
    with pytest.raises(PriceInputError):
        price_lines({"lines": []}, fake_get)
    with pytest.raises(PriceInputError):
        price_lines({"lines": [{"line_id": "1"}]}, fake_get)
    with pytest.raises(PriceInputError):
        price_lines({"lines": [{"line_id": "1", "description": "tv"}], "currency": "EUR"}, fake_get)


def test_marketplace_sellers_on_a_retailer_are_set_aside():
    answer = {"shopping_results": [
        {"title": "Storage Cabinet", "source": "Walmart - MMYGU", "price": "$303.94", "extracted_price": 303.94,
         "link": "https://w/1"},
        {"title": "Storage Cabinet", "source": "Walmart", "price": "$149.00", "extracted_price": 149.0, "link": "https://w/2"},
        {"title": "Storage Cabinet", "source": "B&H Photo-Video-Audio", "price": "$155.00", "extracted_price": 155.0,
         "link": "https://bh/3"},
    ]}
    r = price_lines({"lines": [{"line_id": "1", "description": "storage cabinet"}]}, lambda p: answer)
    line = r["lines"][0]
    assert [k["source"] for k in line["kept"]] == ["Walmart", "B&H Photo-Video-Audio"]
    assert "trusted list" in line["set_aside"][0]["why"]


def test_a_wide_spread_of_listings_is_flagged():
    answer = {"shopping_results": [
        {"title": "Wooden Table", "source": "West Elm", "price": "$479.20", "extracted_price": 479.2, "link": "https://a"},
        {"title": "Wooden Table", "source": "Pottery Barn", "price": "$1,198.99", "extracted_price": 1198.99, "link": "https://b"},
        {"title": "Wooden Table", "source": "Macy's", "price": "$1,079.00", "extracted_price": 1079.0, "link": "https://c"},
    ]}
    r = price_lines({"lines": [{"line_id": "1", "description": "wooden table"}]}, lambda p: answer)
    assert any(f.startswith("wide spread") for f in r["lines"][0]["flags"])


def test_titles_without_the_model_fall_back_to_brand_and_size_with_a_flag():
    answer = {"shopping_results": [
        {"title": "TCL - 75\" Class S4 Series 4K UHD HDR LED Smart TV with Google TV", "source": "Best Buy",
         "price": "$579.99", "extracted_price": 579.99, "link": "https://bestbuy.example/s4"},
        {"title": "TCL 75-Inch Class S4 4K Smart Google TV", "source": "Walmart", "price": "$548.00",
         "extracted_price": 548.0, "link": "https://walmart.example/s4"},
        {"title": "Hisense 75\" A6 Series 4K TV", "source": "Best Buy", "price": "$499.99",
         "extracted_price": 499.99, "link": "https://bestbuy.example/hisense"},
        {"title": "TCL 65\" Class S4 4K TV", "source": "Target", "price": "$399.99",
         "extracted_price": 399.99, "link": "https://target.example/65"},
    ]}
    calls = []
    r = price_lines({"lines": [{"line_id": "W1", "description": "television", "brand_model": "TCL 75S451"}]},
                    lambda p: calls.append(p) or answer, today=date(2026, 11, 27))
    tv = r["lines"][0]
    assert len(calls) == 1                                   # chosen again from the same answer
    assert sorted(k["source"] for k in tv["kept"]) == ["Best Buy", "Walmart"]
    assert any("no listing title names the model TCL 75S451" in f for f in tv["flags"])
    why = {s["title"]: s["why"] for s in tv["set_aside"]}
    assert "lacks: tcl" in why["Hisense 75\" A6 Series 4K TV"]
    assert "lacks: 75" in why["TCL 65\" Class S4 4K TV"]


def test_fallback_that_keeps_nothing_says_so():
    answer = {"shopping_results": [
        {"title": "Hisense 65\" A6 Series 4K TV", "source": "Best Buy", "price": "$399.99",
         "extracted_price": 399.99, "link": "https://bestbuy.example/hisense"}]}
    r = price_lines({"lines": [{"line_id": "W1", "description": "television", "brand_model": "TCL 75S451"}]},
                    lambda p: answer, today=date(2026, 11, 27))
    tv = r["lines"][0]
    assert tv["replacement_cost"] is None
    assert any("none matched tcl and 75 either" in f for f in tv["flags"])
