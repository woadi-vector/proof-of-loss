"""Comparables carry currency and market; prices are never converted or mixed."""

from __future__ import annotations

from datetime import date

from pol.lkq import like_kind_check
from pol.pricing import Comparable, price_from_comps
from pol.records import ScheduleLine, Sighting
from pol.valuation import value_line


def comp(price, currency="CAD", region="CA", n=[0]):
    n[0] += 1
    return Comparable(source=f"Store {n[0]}", url=f"https://example.com/{n[0]}", price=price,
                      currency=currency, region=region)


def test_canadian_claim_keeps_only_canadian_listings():
    pr = price_from_comps([comp(1000), comp(1100), comp(900, "USD", "US")], currency="CAD", region="CA")
    assert pr.replacement_cost == 1050.0 and pr.currency == "CAD"
    assert len(pr.sources) == 2 and len(pr.excluded) == 1
    assert any("another currency" in f and "never converted" in f for f in pr.flags)
    assert pr.basis == "average of 2 listings (CAD 1,000.00 to CAD 1,100.00)"


def test_usd_listing_on_canadian_site_is_still_excluded_by_currency():
    pr = price_from_comps([comp(1000), comp(950, "USD", "CA")], currency="CAD")
    assert pr.replacement_cost == 1000.0


def test_right_currency_wrong_market_is_excluded():
    pr = price_from_comps([comp(1000), comp(1050), comp(980, "CAD", "US")], currency="CAD", region="CA")
    assert len(pr.sources) == 2
    assert any("outside the CA market" in f for f in pr.flags)


def test_unstated_currency_is_set_aside_when_one_is_required():
    pr = price_from_comps([comp(1000), comp(1020, "", "")], currency="CAD")
    assert len(pr.sources) == 1
    assert any("no currency recorded" in f for f in pr.flags)


def test_mixed_currencies_with_none_asked_are_refused():
    pr = price_from_comps([comp(1000, "CAD"), comp(800, "USD")])
    assert pr.replacement_cost is None
    assert any("mix currencies" in f for f in pr.flags)


def test_old_callers_without_currency_unchanged():
    plain = [Comparable("A", "https://a", 100.0), Comparable("B", "https://b", 120.0)]
    pr = price_from_comps(plain)
    assert pr.replacement_cost == 110.0 and pr.basis == "average of 2 listings ($100.00 to $120.00)"


def test_value_line_and_lkq_pass_currency_through():
    line = ScheduleLine("L1", "Den", "Sofa", "couch", "", 1, 2022, 2600.0)
    v = value_line(line, [comp(1500), comp(1600), comp(1200, "USD", "US")], currency="CAD", region="CA",
                   as_of=date(2026, 11, 15))
    assert v.replacement_cost == 1550.0
    s = Sighting("S1", "Den", "grey sofa", line_id="L1", brand_model="Brookline BH-SEC4")
    f = like_kind_check(line, s, [comp(1500), comp(1600), comp(1200, "USD", "US")], currency="CAD", region="CA")
    assert f.observed_replacement == 1550.0 and f.exceeds
    assert "CAD 2,600.00 per unit" in f.explained and "averages CAD 1,550.00" in f.explained
