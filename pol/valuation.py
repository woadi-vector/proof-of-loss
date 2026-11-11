"""Price and depreciate each schedule line, and total what the walk documents.

The rules come from Proof of Loss (pol/schedule.py, pol/pricing.py) unchanged:
  * no price without sources: a receipt, or the average of cited comparables
    computed in pricing.py; a model never writes the number
  * depreciation is a visible straight-line table, capped at 80%, and every
    line explains itself in plain words, with recoverable depreciation shown
  * age comes from the stated purchase year; with none, the earliest submitted
    photo, flagged as a minimum

What changes on the carrier side: the claimed amount sits beside the documented
price, and the gap is shown as a number. A gap is a fact for the investigator,
never a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .pricing import Comparable, explain_depreciation, price_from_comps
from .records import LineResult, ScheduleLine, Status
# One useful-life table for the whole package; a carrier replaces it with its own.
from .schedule import DEFAULT_LIFE_YEARS, FALLBACK_LIFE_YEARS, MAX_DEPRECIATION


@dataclass
class Valuation:
    line_id: str
    quantity: int
    replacement_cost: float | None          # per unit
    price_basis: str
    price_sources: list[Comparable]
    age_years: float | None
    useful_life_years: float
    depreciation_pct: float | None
    actual_cash_value: float | None         # per unit
    recoverable_depreciation: float | None  # per unit
    depreciation_explained: str
    claimed_total: float | None
    documented_total: float | None          # replacement cost x quantity
    gap: float | None                       # claimed_total - documented_total
    flags: list[str] = field(default_factory=list)


def value_line(line: ScheduleLine, comps: list[Comparable] | None = None,
               receipt: float | None = None, life: dict[str, float] | None = None,
               as_of: date | None = None, age_note: str | None = None) -> Valuation:
    """`age_note` replaces the default "Bought <year>, per the schedule." sentence,
    for ages that come from somewhere else (an adjuster's estimate on the walk)."""
    life = life or DEFAULT_LIFE_YEARS
    as_of = as_of or date.today()
    flags: list[str] = []

    if receipt is not None:
        rcv, basis, sources = receipt, "receipt", []
    else:
        pr = price_from_comps(comps or [])
        rcv, basis, sources = pr.replacement_cost, pr.basis, pr.sources
        flags.extend(pr.flags if comps else [])
    if rcv is None:
        flags.append("replacement cost needed: no receipt and no cited listings")

    if line.purchase_year:
        age = float(max(0, as_of.year - line.purchase_year))
        age_basis = age_note or f"Bought {line.purchase_year}, per the schedule."
    elif line.photo_dates:
        earliest = min(line.photo_dates)
        age = max(0.0, (as_of - earliest).days / 365.25)
        age_basis = f"No purchase year given; earliest submitted photo is from {earliest:%B %Y}."
        flags.append("age from earliest photo (minimum)")
    else:
        age, age_basis = None, ""
        flags.append("no purchase year and no dated photo")

    years = life.get(line.category, FALLBACK_LIFE_YEARS)
    raw = age / years if age is not None else None
    dep = min(MAX_DEPRECIATION, raw) if raw is not None else None
    capped = raw is not None and raw > MAX_DEPRECIATION
    acv = round(rcv * (1 - dep), 2) if (rcv is not None and dep is not None) else None
    held = round(rcv - acv, 2) if acv is not None else None

    claimed_total = (round(line.claimed_amount * line.quantity, 2)
                     if line.claimed_amount is not None else None)
    documented_total = round(rcv * line.quantity, 2) if rcv is not None else None
    gap = (round(claimed_total - documented_total, 2)
           if claimed_total is not None and documented_total is not None else None)

    explained = (age_basis + " " if age_basis else "") + explain_depreciation(
        age, years, dep, capped, rcv, acv)
    return Valuation(line.line_id, line.quantity, rcv, basis, sources,
                     round(age, 1) if age is not None else None, years,
                     round(dep * 100, 1) if dep is not None else None, acv, held,
                     explained, claimed_total, documented_total, gap, flags)


def documented_value(results: list[LineResult], valuations: dict[str, Valuation],
                     accepted: set[str] | None = None) -> dict:
    """The figure Clarice's OVER_VALUE rule compares the claim against.

    Counts a line only if it is Consistent or a person accepted it in review,
    and it has a documented price. Everything else is counted separately, so
    the rule never fires on unverified numbers.
    """
    accepted = accepted or set()
    asset = acv_total = 0.0
    counted, excluded = 0, {}
    unpriced = 0
    for r in results:
        if r.line_id is None:
            continue
        ok = r.status is Status.CONSISTENT or r.line_id in accepted
        v = valuations.get(r.line_id)
        if not ok:
            excluded[r.status.value] = excluded.get(r.status.value, 0) + 1
            continue
        if v is None or v.documented_total is None:
            unpriced += 1
            continue
        counted += 1
        asset += v.documented_total
        if v.actual_cash_value is not None:
            acv_total += v.actual_cash_value * v.quantity
    return {"assetValue": round(asset, 2), "actualCashValue": round(acv_total, 2),
            "linesCounted": counted, "linesUnpriced": unpriced, "linesExcluded": excluded}
