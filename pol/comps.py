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
