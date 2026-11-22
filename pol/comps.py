"""Find current listings for a claimed item, with every price traceable to a page.

A model with web search proposes listings; code decides what is kept:

  * every proposed page is fetched; a listing is kept when its page is one the
    search actually returned (grounding sources, followed through Google's
    redirect links), or when the page opens now and shows the listed price.
    A page that is neither cited nor showing the price is dropped as
    unverified, never repaired or guessed at
  * each kept page is saved as a snapshot with its SHA-256; a cited listing
    whose price is not found on the page is kept but marked, for a person to
    check before it is used
  * the model never writes the replacement cost: pol/pricing.py averages the
    kept listings, sets aside outliers and other currencies, and cites them

Only the item description (and brand or model, if given) is sent to the model.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from .pricing import Comparable
from .providers import Ledger


@dataclass
class Page:
    final_url: str
    status: int
    text: str


Fetch = Callable[[str], Page | None]


@dataclass
class Proposed:
    source: str
    url: str
    title: str
    price: float
    currency: str


@dataclass
class SearchAnswer:
    assumptions: str
    listings: list[Proposed]
    cited: list[str]            # grounding source links as returned (often redirects)
    queries: list[str]          # web searches the model ran


class ListingFinder(Protocol):
    name: str

    def find_listings(self, item: str, currency: str, region: str, ledger: Ledger) -> SearchAnswer: ...


@dataclass
class Dropped:
    listing: Proposed
    reason: str


@dataclass
class FindResult:
    line_id: str
    item: str
    kept: list[Comparable]
    dropped: list[Dropped]
    assumptions: str
    queries: list[str]
    snapshots: list[dict] = field(default_factory=list)
    cited_pages: list[str] = field(default_factory=list)   # where the search went, after redirects
    answer_sha256: str = ""  # SHA-256 of the raw search answer, the backup for every kept listing


def norm_url(u: str) -> str:
    """Host without www, path without trailing slash; query and fragment dropped."""
    p = urlsplit(u.strip())
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}"


def _price_forms(price: float) -> list[str]:
    whole = f"{price:,.2f}"
    forms = {whole, whole.replace(",", ""), f"{price:.2f}"}
    if price == int(price):
        forms |= {f"{int(price):,}", str(int(price))}
    return sorted(forms, key=len, reverse=True)


def price_on_page(text: str, price: float) -> bool:
    """True when the listed price appears on the page as a number (not inside a longer one)."""
    for f in _price_forms(price):
        if re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d]|[.,]\d)", text):
            return True
    return False


def default_fetch(url: str) -> Page | None:
    import requests
    try:
        r = requests.get(url, timeout=20, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
                                  "Accept-Language": "en-CA,en;q=0.9"})
        return Page(r.url, r.status_code, r.text)
    except Exception:
        return None


def find_comps(line_id: str, item: str, finder: ListingFinder, ledger: Ledger,
               currency: str = "CAD", region: str = "CA", fetch: Fetch = default_fetch,
               snapshot_dir: Path | None = None, today: date | None = None) -> FindResult:
    today = today or date.today()
    ans = finder.find_listings(item, currency, region, ledger)

    # Where the search actually went: follow each grounding link to its page.
    cited: set[str] = set()
    resolved: list[str] = []
    for u in ans.cited:
        cited.add(norm_url(u))
        pg = fetch(u) if "grounding-api-redirect" in u else None
        if pg and pg.final_url:
            cited.add(norm_url(pg.final_url))
            resolved.append(pg.final_url)
        else:
            resolved.append(u)

    res = FindResult(line_id, item, [], [], ans.assumptions, ans.queries, cited_pages=resolved)
    seen: set[str] = set()
    for p in ans.listings:
        key = norm_url(p.url) if p.url else ""
        if not p.url or not p.source or not p.price or p.price <= 0:
            res.dropped.append(Dropped(p, "missing store, link or price"))
            continue
        if key in seen:
            res.dropped.append(Dropped(p, "same page listed twice"))
            continue
        seen.add(key)
        is_cited = key in cited
        pg = fetch(p.url)
        opened = pg is not None and pg.status < 400 and bool(pg.text)
        shows_price = opened and price_on_page(pg.text, p.price)
        if not is_cited and not shows_price:
            why = ("page not among the search results, and it could not be opened to check"
                   if not opened else
                   "page not among the search results, and the listed price is not on it")
            res.dropped.append(Dropped(p, why))
            continue

        if not opened:
            note = "page could not be opened to confirm the price"
        elif not shows_price:
            note = "price not found on the page; check before use"
        else:
            note = "" if is_cited else "price confirmed on the page (page found by the model, not a search result)"
        if opened and snapshot_dir is not None:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            body = pg.text.encode("utf-8", "replace")
            sha = hashlib.sha256(body).hexdigest()
            name = f"line{line_id}_{len(res.kept) + 1}_{sha[:10]}.html"
            (snapshot_dir / name).write_bytes(body)
            res.snapshots.append({"line": line_id, "url": p.url, "final_url": pg.final_url, "file": name,
                                  "sha256": sha, "retrieved": today.isoformat()})
        res.kept.append(Comparable(source=p.source, url=p.url, price=round(p.price, 2), title=p.title,
                                   retrieved=today.isoformat(), currency=(p.currency or "").upper(),
                                   region=region.upper(), note=note))
    return res


# Model side --------------------------------------------------------------------

LISTINGS_PROMPT = """Find current listings to replace this item, new, of like kind and quality.

Item: {item}
Market: {region}. Prices in {currency}.

Search the web. Return 3 to 5 listings from stores that sell in that market,
for the same item or the closest current equivalent (same brand and line when
the brand is given). Use only pages from your search results. Never invent a
store, a link or a price; if you find fewer than 3, return fewer.

For each listing give: source (store name), url (the exact product page),
title (as written on the page), price (regular selling price as a number, no
currency sign), currency (ISO code as shown on the page).

If the description is too vague to pin down one product (size, model, material),
say what you assumed in "assumptions".

Answer with JSON only:
{{"assumptions": "...", "listings": [{{"source": "", "url": "", "title": "", "price": 0, "currency": ""}}]}}"""


def parse_answer(text: str) -> tuple[str, list[Proposed]]:
    t = text.strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError(f"no JSON object in the answer: {t[:200]!r}")
    d = json.loads(t[a:b + 1])
    out = []
    for x in d.get("listings", []) or []:
        try:
            price = float(str(x.get("price", "")).replace(",", "").replace("$", "").strip() or 0)
        except ValueError:
            price = 0.0
        out.append(Proposed(str(x.get("source", "")).strip(), str(x.get("url", "")).strip(),
                            str(x.get("title", "")).strip(), price, str(x.get("currency", "")).strip()))
    return str(d.get("assumptions", "") or "").strip(), out


# Google Shopping through SerpApi -------------------------------------------------

SERPAPI_URL = "https://serpapi.com/search.json"


def serpapi_key() -> str:
    import os

    from .providers import _env_file
    return os.environ.get("SERPAPI_KEY") or _env_file().get("SERPAPI_KEY", "")


def _shopping_currency(price_text: str, region: str) -> str:
    """Currency as Google Shopping showed it. Only a Canadian search with a plain or
    CA$ sign counts as CAD; anything else is left blank and pricing sets it aside."""
    t = price_text.replace(" ", " ").strip()
    if region.upper() == "CA" and (t.startswith("CA$") or t.startswith("C$") or t.startswith("$")):
        return "CAD"
    if region.upper() == "US" and t.startswith("$"):
        return "USD"
    return ""


# Stores an adjuster will accept as a new-retail price in Canada: the maker's own
# store and established Canadian retailers. Marketplaces, resale and overseas
# sellers are set aside (kept in the log for review). Extend per claim with a
# stores.txt next to the lines file, one store name per line.
TRUSTED_STORES_CA = [
    "best buy canada", "bestbuy.ca", "walmart.ca", "walmart canada", "canadian tire", "costco",
    "the home depot", "home depot canada", "lowe's", "rona", "staples", "london drugs", "visions",
    "the brick", "leon's", "structube", "ikea", "wayfair", "eq3", "article", "sleep country",
    "amazon.ca", "simons", "holt renfrew", "ssense", "harry rosen", "sporting life", "sport chek",
    "atmosphere", "mark's", "aritzia", "sephora", "shoppers drug mart", "the source", "apple",
    "dyson", "sony", "samsung", "lg", "celine", "louis vuitton", "saint laurent", "ysl", "dior",
    "hermès", "hermes", "loro piana", "alo", "lululemon", "massimo dutti", "zara", "lacoste",
    "kate spade", "ted baker", "timberland", "pajar", "cougar", "golf town", "bed bath & beyond",
    "hudson's bay", "the bay", "nordstrom", "saks", "brandsmart", "best buy",
]
# The same rule for the United States: the maker's own store and established US
# retailers. Amazon counts only as "Amazon.com" itself (third-party sellers show
# under their own names and are set aside).
TRUSTED_STORES_US = [
    "best buy", "bestbuy.com", "walmart", "walmart.com", "target", "the home depot", "home depot",
    "lowe's", "lowes", "costco", "sam's club", "amazon.com", "b&h photo", "b&h photo-video-audio",
    "adorama", "crutchfield", "newegg", "staples", "office depot", "ikea", "wayfair", "crate & barrel",
    "pottery barn", "west elm", "ashley", "rooms to go", "la-z-boy", "macy's", "kohl's", "nordstrom",
    "jcpenney", "dick's sporting goods", "academy sports", "rei", "bed bath & beyond", "overstock",
    "apple", "samsung", "lg", "sony", "tcl", "hisense", "vizio", "dell", "hp", "lenovo", "bose",
    "sonos", "dyson", "kitchenaid", "whirlpool", "ge appliances", "proform", "ifit", "peloton",
    "nordictrack", "bowflex", "dewalt", "milwaukee", "ryobi", "harbor freight", "ace hardware",
    "menards", "tractor supply", "petsmart", "petco", "gamestop", "microsoft",
]
NOT_NEW = ("refurb", "remis à neuf", "remis a neuf", "re-certified", "recertified", "open box",
           "pre-owned", "preowned", "used", "déstockage", "destockage", "reconditionn", "renewed")
PARTS = ("attachment", "accessor", "replacement", "brush tool", "brush head", "filter", "spare",
         "parts", "strap only", "dust bag", "charger", "battery", "case for", "cover for",
         "wall mount", "mount for", "stand for", "remote for", "replacement remote", "screen protector",
         "cable for", "power cord for")
MARKETPLACE = ("market", "marketplace", "ebay", "poshmark", "etsy", "aliexpress", "temu", "desertcart")


def _trusted(source: str, stores: list[str]) -> bool:
    s = source.lower().strip()
    if any(m in s for m in MARKETPLACE):
        return False
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", s) for t in stores)


def select_shopping(data: dict, line_id: str, item: str, currency: str = "CAD", region: str = "CA",
                    max_keep: int = 5, must: list[str] | None = None, stores: list[str] | None = None,
                    today: date | None = None, search_note: str = "",
                    exclude: list[str] | None = None) -> FindResult:
    """Choose comparables from one Google Shopping answer. Kept: new items from a
    trusted store whose title has every required word, priced as shown. Every
    other result is set aside with its reason, never edited."""
    today = today or date.today()
    stores = [x.lower() for x in (stores or (TRUSTED_STORES_US if region.upper() == "US" else TRUSTED_STORES_CA))]
    must = [w.lower() for w in (must or []) if w.strip()]
    exclude = [w.lower() for w in (exclude or []) if w.strip()]
    res = FindResult(line_id, item, [], [], search_note, [item])
    seen: set = set()
    for r in data.get("shopping_results", []) or []:
        price = r.get("extracted_price")
        url = r.get("link") or r.get("product_link") or ""
        title = str(r.get("title", "")).strip()
        p = Proposed(str(r.get("source", "")).strip(), url, title,
                     float(price) if isinstance(price, (int, float)) else 0.0,
                     _shopping_currency(str(r.get("price", "")), region))
        t = title.lower()
        if not p.source or not p.url or p.price <= 0:
            why = "missing store, link or price"
        elif r.get("second_hand_condition") or any(w in t for w in NOT_NEW):
            why = f"not new ({r.get('second_hand_condition') or 'title says so'})"
        elif any(w in t for w in PARTS):
            why = "a part or accessory, not the item"
        elif exclude and any(w in t for w in exclude):
            why = f"title has an excluded word: {', '.join(w for w in exclude if w in t)}"
        elif must and not all(w in t for w in must):
            why = f"title lacks: {', '.join(w for w in must if w not in t)}"
        elif not _trusted(p.source, stores):
            why = "store not on the trusted list (marketplace, resale or unknown seller)"
        elif (p.source.lower(), round(p.price, 2)) in seen:
            why = "same store and price listed twice"
        elif len(res.kept) >= max_keep:
            why = f"beyond the first {max_keep} kept"
        else:
            why = ""
        if why:
            res.dropped.append(Dropped(p, why))
            continue
        seen.add((p.source.lower(), round(p.price, 2)))
        note = "price as shown on Google Shopping " + region.upper()
        if search_note:
            note += f"; {search_note}"
        res.kept.append(Comparable(source=p.source, url=p.url, price=round(p.price, 2), title=title,
                                   retrieved=today.isoformat(), currency=p.currency,
                                   region=region.upper(), note=note))
    return res


def find_shopping(line_id: str, item: str, get_json: Callable[[dict], dict], currency: str = "CAD",
                  region: str = "CA", max_keep: int = 5, snapshot_dir: Path | None = None,
                  today: date | None = None, search_note: str = "", must: list[str] | None = None,
                  stores: list[str] | None = None, exclude: list[str] | None = None) -> FindResult:
    """One Google Shopping search, saved whole with its SHA-256 as the backup, then
    select_shopping picks the comparables."""
    today = today or date.today()
    params = {"engine": "google_shopping", "q": item, "gl": region.lower(), "hl": "en",
              "google_domain": "google.ca" if region.upper() == "CA" else "google.com"}
    data = get_json(params)
    if data.get("error"):
        raise RuntimeError(f"SerpApi: {data['error']}")
    res = select_shopping(data, line_id, item, currency, region, max_keep, must, stores, today, search_note,
                          exclude)
    raw = json.dumps(data, sort_keys=True).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    res.answer_sha256 = sha
    if snapshot_dir is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        name = f"line{line_id}_shopping_{sha[:10]}.json"
        (snapshot_dir / name).write_bytes(raw)
        meta = data.get("search_metadata", {})
        res.snapshots.append({"line": line_id, "file": name, "sha256": sha, "retrieved": today.isoformat(),
                              "search_id": meta.get("id", ""),
                              "google_url": meta.get("google_shopping_url", "")})
    return res


def serpapi_get(api_key: str) -> Callable[[dict], dict]:
    import requests

    def get(params: dict) -> dict:
        r = requests.get(SERPAPI_URL, params={**params, "api_key": api_key}, timeout=60)
        try:
            return r.json()
        except ValueError:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return get
