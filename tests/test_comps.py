"""Comps finder with a fake search and fake pages: what is kept, dropped and marked. No network."""

from datetime import date
from types import SimpleNamespace as NS

from pol.comps import Page, Proposed, SearchAnswer, find_comps, norm_url, parse_answer, price_on_page
from pol.pricing import price_from_comps
from pol.providers import Ledger

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


class FakeFinder:
    name = "fake"

    def __init__(self, ans):
        self.ans = ans

    def find_listings(self, item, currency, region, ledger):
        ledger.add(NS(cached=False, cost_usd=0.001, seconds=1.0))
        return self.ans


PAGES = {
    REDIRECT + "a": Page("https://www.storea.ca/p/tote-123?utm=x", 200, "<html>Tote $6,500.00 CAD</html>"),
    REDIRECT + "b": Page("https://storeb.ca/item/tote/", 200, "<html>price 6200</html>"),
    "https://storea.ca/p/tote-123": Page("https://storea.ca/p/tote-123", 200, "<html>Tote $6,500.00 CAD</html>"),
    "https://storeb.ca/item/tote": Page("https://storeb.ca/item/tote", 200, '{"price": "6,350.00"}'),
}


def test_only_cited_pages_are_kept_and_unconfirmed_prices_are_marked(tmp_path):
    ans = SearchAnswer("assumed medium size", [
        Proposed("Store A", "https://storea.ca/p/tote-123", "Tote", 6500.0, "CAD"),
        Proposed("Store B", "https://storeb.ca/item/tote", "Tote", 6200.0, "CAD"),
        Proposed("Made Up", "https://nowhere.ca/tote", "Tote", 5000.0, "CAD"),
        Proposed("Store A", "https://storea.ca/p/tote-123/", "Tote again", 6500.0, "CAD"),
        Proposed("No price", "https://storeb.ca/item/tote", "Tote", 0, "CAD"),
    ], [REDIRECT + "a", REDIRECT + "b"], ["celine luggage tote price canada"])
    r = find_comps("59", "Celine tote bag", FakeFinder(ans), Ledger(), fetch=PAGES.get,
                   snapshot_dir=tmp_path / "pages", today=date(2026, 11, 17))
    assert [c.source for c in r.kept] == ["Store A", "Store B"]
    a, b = r.kept
    assert a.note == "" and a.retrieved == "2026-11-17" and a.region == "CA"
    assert b.note == "price not found on the page; check before use", "page says 6,350, listing said 6,200"
    reasons = sorted(d.reason for d in r.dropped)
    assert reasons == ["missing store, link or price",
                       "page not among the search results, and it could not be opened to check",
                       "same page listed twice"]
    assert r.cited_pages == ["https://www.storea.ca/p/tote-123?utm=x", "https://storeb.ca/item/tote/"]
    assert len(r.snapshots) == 2 and all(len(s["sha256"]) == 64 for s in r.snapshots)
    assert (tmp_path / "pages" / r.snapshots[0]["file"]).exists()
    # The model never sets the number: pricing averages what was kept.
    assert price_from_comps(r.kept, "CAD", "CA").replacement_cost == 6350.0


def test_page_that_will_not_open_is_kept_but_marked(tmp_path):
    ans = SearchAnswer("", [Proposed("Store A", "https://storea.ca/p/tote-123", "Tote", 6500.0, "CAD")],
                       ["https://storea.ca/p/tote-123"], [])
    r = find_comps("59", "tote", FakeFinder(ans), Ledger(), fetch=lambda u: None)
    assert r.kept[0].note == "page could not be opened to confirm the price"


def test_price_matching_is_exact_not_a_substring():
    assert price_on_page("Now $1,299.99", 1299.99)
    assert price_on_page("price: 1299", 1299.0)
    assert not price_on_page("SKU 112990", 1299.0)
    assert not price_on_page("$1,299.50", 1299.0)


def test_norm_url_and_parse():
    assert norm_url("https://WWW.Store.ca/a/b/?x=1#y") == "store.ca/a/b"
    text = 'Here you go:\n```json\n{"assumptions": "none", "listings": [{"source": "S", "url": "https://s.ca/x", ' \
           '"title": "T", "price": "1,050.00", "currency": "cad"}]}\n```'
    assumptions, listings = parse_answer(text)
    assert assumptions == "none" and listings[0].price == 1050.0 and listings[0].currency == "cad"


def test_uncited_page_is_kept_only_when_it_shows_the_price(tmp_path):
    pages = {"https://shop.ca/a": Page("https://shop.ca/a", 200, "<b>$549.99</b>"),
             "https://shop.ca/b": Page("https://shop.ca/b", 200, "<b>$499.99</b>"),
             "https://shop.ca/c": Page("https://shop.ca/c", 404, "")}
    ans = SearchAnswer("", [Proposed("Shop", "https://shop.ca/a", "V8", 549.99, "CAD"),
                            Proposed("Shop", "https://shop.ca/b", "Ball", 599.99, "CAD"),
                            Proposed("Shop", "https://shop.ca/c", "Gone", 300.0, "CAD")], [], [])
    r = find_comps("31", "Dyson vacuum", FakeFinder(ans), Ledger(), fetch=pages.get, snapshot_dir=tmp_path)
    assert [c.url for c in r.kept] == ["https://shop.ca/a"]
    assert r.kept[0].note.startswith("price confirmed on the page")
    assert sorted(d.reason for d in r.dropped) == [
        "page not among the search results, and it could not be opened to check",
        "page not among the search results, and the listed price is not on it"]


def test_shopping_keeps_new_listings_as_shown_and_saves_the_raw_answer(tmp_path):
    from pol.comps import find_shopping
    answer = {"search_metadata": {"id": "abc", "google_shopping_url": "https://www.google.ca/search?q=x"},
              "shopping_results": [
                  {"title": "Dyson V15 Detect", "source": "Best Buy Canada", "price": "$999.99",
                   "extracted_price": 999.99, "product_link": "https://www.google.com/shopping/product/1?gl=ca"},
                  {"title": "Dyson V15 Detect", "source": "Dyson", "price": "CA$1,049.99",
                   "extracted_price": 1049.99, "link": "https://www.dysoncanada.ca/v15"},
                  {"title": "Dyson V15 (used)", "source": "eBay", "price": "$450.00", "extracted_price": 450.0,
                   "product_link": "https://g/2", "second_hand_condition": "used"},
                  {"title": "Dyson V15 Detect", "source": "Best Buy Canada", "price": "$999.99",
                   "extracted_price": 999.99, "product_link": "https://g/3"},
                  {"title": "no price", "source": "X", "price": "", "product_link": "https://g/4"},
              ]}
    seen = {}
    r = find_shopping("31", "Dyson V15 Detect", lambda p: (seen.update(p), answer)[1], snapshot_dir=tmp_path,
                      today=date(2026, 11, 17), search_note="searched as 'Dyson V15 Detect'")
    assert seen["gl"] == "ca" and seen["engine"] == "google_shopping"
    assert [(c.source, c.price, c.currency) for c in r.kept] == [("Best Buy Canada", 999.99, "CAD"),
                                                                  ("Dyson", 1049.99, "CAD")]
    assert r.kept[1].url == "https://www.dysoncanada.ca/v15", "a direct store link wins over Google's page"
    assert "Google Shopping CA" in r.kept[0].note and "searched as" in r.kept[0].note
    assert sorted(d.reason for d in r.dropped) == ["missing store, link or price", "not new (used)",
                                                   "same store and price listed twice"]
    snap = r.snapshots[0]
    assert snap["search_id"] == "abc" and (tmp_path / snap["file"]).exists()
    assert price_from_comps(r.kept, "CAD", "CA").replacement_cost == 1024.99


def test_shopping_error_is_raised_not_guessed():
    import pytest

    from pol.comps import find_shopping
    with pytest.raises(RuntimeError, match="Invalid API key"):
        find_shopping("1", "x", lambda p: {"error": "Invalid API key"})


def test_selection_keeps_trusted_new_matching_listings_only():
    from pol.comps import select_shopping
    data = {"shopping_results": [
        {"title": "CELINE Big Luggage In Grained Calfskin", "source": "CELINE", "price": "$6,300.00",
         "extracted_price": 6300.0, "product_link": "https://g/1"},
        {"title": "Celine Mini Luggage Tote", "source": "Bessette", "price": "$3,754.00",
         "extracted_price": 3754.0, "product_link": "https://g/2"},
        {"title": "Celine Micro Luggage Tote", "source": "Poshmark Canada", "price": "$1,525.00",
         "extracted_price": 1525.0, "product_link": "https://g/3", "second_hand_condition": "pre-owned"},
        {"title": "Celine Triomphe Cabas", "source": "CELINE", "price": "$3,250.00",
         "extracted_price": 3250.0, "product_link": "https://g/4"},
        {"title": "Dyson V15 Detect - Remis à neuf", "source": "Walmart.ca", "price": "$699.99",
         "extracted_price": 699.99, "product_link": "https://g/5"},
        {"title": "Celine Luggage replacement strap only", "source": "CELINE", "price": "$400.00",
         "extracted_price": 400.0, "product_link": "https://g/6"},
        {"title": "Celine Luggage", "source": "Best Buy Canada Marketplace", "price": "$5,000.00",
         "extracted_price": 5000.0, "product_link": "https://g/7"},
    ]}
    r = select_shopping(data, "59", "Celine Luggage tote", must=["luggage"])
    assert [(c.source, c.price) for c in r.kept] == [("CELINE", 6300.0)]
    reasons = {d.listing.product if hasattr(d.listing, "product") else d.listing.url: d.reason for d in r.dropped}
    assert reasons["https://g/2"].startswith("store not on the trusted list")
    assert reasons["https://g/3"].startswith("not new")
    assert reasons["https://g/4"] == "title lacks: luggage"
    assert reasons["https://g/5"].startswith("not new")
    assert reasons["https://g/6"] == "a part or accessory, not the item"
    assert reasons["https://g/7"].startswith("store not on the trusted list")


def test_store_names_match_as_words():
    from pol.comps import _trusted
    assert _trusted("Best Buy Canada", ["best buy canada"])
    assert not _trusted("Halo Boutique", ["alo"])
    assert not _trusted("Bulgari", ["lg"])
