"""Replacement cost from comparable listings.

Price is where contents claims stall: the adjuster offers less, the claimant
has no backup, and the haggling runs for weeks. The answer is the same thing
an adjuster's own pricing vendor does: several current listings for an item of
like kind and quality, each one cited, and a number computed from them that
either side can check.

Rules:
  * every comparable carries a source, a URL and a price; a comp without all
    three is not counted
  * code computes the figure; no model writes a number into the schedule
  * the claimant's own receipt or stated price outranks comparables
  * with three or more comps, listings far from the median (under 40% or over
    250% of it) are set aside as not like kind and quality, and the row says so
  * fewer than two usable comps is flagged as thin support
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median

OUTLIER_LOW = 0.40
OUTLIER_HIGH = 2.50
MIN_COMPS = 2


@dataclass
class Comparable:
    source: str        # store or site, e.g. "Best Buy"
    url: str
    price: float
    title: str = ""    # listing title, for like-kind-and-quality review
    retrieved: str = ""  # ISO date the price was seen
    currency: str = ""   # ISO code as listed, e.g. "CAD"; "" when not recorded
    region: str = ""     # market the listing sells in, e.g. "CA"; "" when not recorded
    note: str = ""       # what a reviewer should know, e.g. price not confirmed on the page


@dataclass
class PriceResult:
    replacement_cost: float | None
    basis: str          # human-readable: where the number came from
    sources: list[Comparable]
    excluded: list[Comparable]
    flags: list[str]
    currency: str = ""


def usable(c: Comparable) -> bool:
    return bool(c.source and c.url and c.price and c.price > 0)


def _market_filter(good: list[Comparable], currency: str | None, region: str | None,
                   flags: list[str]) -> tuple[list[Comparable], list[Comparable]]:
    """Keep listings in the claim's currency and market. Never convert a price.

    With no currency asked for, listings that state different currencies are
    refused outright: averaging CAD with USD is meaningless.
    """
    off: list[Comparable] = []
    if currency:
        want = currency.upper()
        wrong = [c for c in good if c.currency.upper() != want]
        if wrong:
            unstated = sum(1 for c in wrong if not c.currency)
            other = len(wrong) - unstated
            if other:
                flags.append(f"{other} listing(s) in another currency set aside; prices are never converted")
            if unstated:
                flags.append(f"{unstated} listing(s) with no currency recorded set aside")
        off += wrong
        good = [c for c in good if c not in wrong]
    else:
        stated = {c.currency.upper() for c in good if c.currency}
        if len(stated) > 1:
            flags.append(f"listings mix currencies ({', '.join(sorted(stated))}); no average taken, "
                         "and prices are never converted")
            return [], good
    if region:
        want_r = region.upper()
        wrong_r = [c for c in good if c.region.upper() != want_r]
        if wrong_r:
            flags.append(f"{len(wrong_r)} listing(s) from outside the {want_r} market "
                         "(or with no market recorded) set aside")
        off += wrong_r
        good = [c for c in good if c not in wrong_r]
    return good, off


def price_from_comps(comps: list[Comparable], currency: str | None = None,
                     region: str | None = None) -> PriceResult:
    """Average cited listings. Pass `currency` and `region` (e.g. "CAD", "CA")
    to keep only listings from the claim's market; nothing is ever converted."""
    good = [c for c in comps if usable(c)]
    flags: list[str] = []
    if len(good) < len(comps):
        flags.append(f"{len(comps) - len(good)} listing(s) missing source, link or price; not counted")
    good, off_market = _market_filter(good, currency, region, flags)
    if not good:
        return PriceResult(None, "", [], off_market, flags + ["no priced comparables"], currency or "")
    cur = (currency or next((c.currency for c in good if c.currency), "")).upper()

    excluded: list[Comparable] = list(off_market)
    if len(good) >= 3:
        mid = median(c.price for c in good)
        kept = [c for c in good if OUTLIER_LOW * mid <= c.price <= OUTLIER_HIGH * mid]
        far = [c for c in good if c not in kept]
        excluded += far
        good = kept
        if far:
            flags.append(f"{len(far)} listing(s) set aside as far from the median price")

    avg = round(mean(c.price for c in good), 2)
    lo, hi = min(c.price for c in good), max(c.price for c in good)
    if len(good) < MIN_COMPS:
        flags.append("thin price support: one listing")
    unit = f"{cur} " if cur else "$"
    span = f"{unit}{lo:,.2f}" if lo == hi else f"{unit}{lo:,.2f} to {unit}{hi:,.2f}"
    basis = f"average of {len(good)} listing{'s' if len(good) != 1 else ''} ({span})"
    return PriceResult(avg, basis, good, excluded, flags, cur)


def explain_depreciation(age_years: float | None, life_years: float, dep: float | None,
                         capped: bool, rcv: float | None, acv: float | None) -> str:
    """One sentence a claimant can read and an adjuster can check."""
    if age_years is None or dep is None:
        return "No age yet, so no depreciation computed. Add a purchase date or a dated photo."
    rate = f"{age_years:.1f} years of a {life_years:g}-year useful life = {dep * 100:.1f}%"
    if capped:
        rate += f" (capped at {dep * 100:.0f}%)"
    if rcv is None:
        return f"Straight-line: {rate}. Needs a replacement cost to compute the dollar amount."
    held = rcv - (acv or 0.0)
    return (f"Straight-line: {rate}. ${rcv:,.2f} replacement cost less ${held:,.2f} depreciation "
            f"= ${acv:,.2f} actual cash value. On a replacement cost policy the ${held:,.2f} is "
            f"recoverable once the item is replaced; keep the receipt.")
