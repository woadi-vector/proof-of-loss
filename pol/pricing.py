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


@dataclass
class PriceResult:
    replacement_cost: float | None
    basis: str          # human-readable: where the number came from
    sources: list[Comparable]
    excluded: list[Comparable]
    flags: list[str]


def usable(c: Comparable) -> bool:
    return bool(c.source and c.url and c.price and c.price > 0)


def price_from_comps(comps: list[Comparable]) -> PriceResult:
    good = [c for c in comps if usable(c)]
    flags: list[str] = []
    if len(good) < len(comps):
        flags.append(f"{len(comps) - len(good)} listing(s) missing source, link or price; not counted")
    if not good:
        return PriceResult(None, "", [], [], flags + ["no priced comparables"])

    excluded: list[Comparable] = []
    if len(good) >= 3:
        mid = median(c.price for c in good)
        kept = [c for c in good if OUTLIER_LOW * mid <= c.price <= OUTLIER_HIGH * mid]
        excluded = [c for c in good if c not in kept]
        good = kept
        if excluded:
            flags.append(f"{len(excluded)} listing(s) set aside as far from the median price")

    avg = round(mean(c.price for c in good), 2)
    lo, hi = min(c.price for c in good), max(c.price for c in good)
    if len(good) < MIN_COMPS:
        flags.append("thin price support: one listing")
    span = f"${lo:,.2f}" if lo == hi else f"${lo:,.2f} to ${hi:,.2f}"
    basis = f"average of {len(good)} listing{'s' if len(good) != 1 else ''} ({span})"
    return PriceResult(avg, basis, good, excluded, flags)


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
