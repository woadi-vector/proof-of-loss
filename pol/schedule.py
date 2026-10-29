"""The schedule of loss: the document the claimant actually files.

Rules this module enforces, whatever any model says:
  * no evidence, no line: every row cites at least one photo and crop
  * no price without sources: replacement cost is the claimant's receipt, or
    the average of cited comparable listings computed in pol/pricing.py; with
    neither, it is left blank and flagged
  * every line explains its own depreciation and shows the recoverable part
  * depreciation uses a visible, editable table, never a hidden guess
  * age comes from the earliest photo the item appears in, which is a floor
    the claimant can document, not an estimate
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import Item
from .pricing import Comparable, explain_depreciation, price_from_comps

# Useful life in years by category, used for straight-line depreciation.
# These are starting defaults for the demo, not any carrier's table: carriers
# publish their own, and the claimant or adjuster should replace these.
DEFAULT_LIFE_YEARS = {
    "television": 7, "speaker": 7, "soundbar": 7, "laptop": 4, "computer": 5,
    "couch": 10, "chair": 10, "bed": 10, "rug": 8, "lamp": 10,
    "table": 15, "side table": 15, "tv stand": 15, "bookshelf": 15,
    "picture frame": 20, "ceiling fan": 15,
}
FALLBACK_LIFE_YEARS = 10
MAX_DEPRECIATION = 0.80   # never depreciate below 20% of replacement cost

COLUMNS = [
    "item_id", "room", "description", "brand_model", "quantity",
    "purchase_year", "earliest_photo_date", "age_years", "purchased_from",
    "replacement_cost", "price_basis", "price_sources",
    "useful_life_years", "depreciation_pct", "depreciation_amount", "actual_cash_value",
    "recoverable_depreciation", "depreciation_explained",
    "evidence_photos", "evidence_crop", "flags",
]


@dataclass
class ClaimantEntry:
    """What the claimant tells us about an item. Everything optional."""
    description: str = ""
    brand_model: str = ""
    room: str = ""
    quantity: int = 1
    purchased_from: str = ""
    purchase_year: int | None = None           # what claimants actually remember
    purchase_date: date | None = None          # exact date if they have a receipt
    replacement_cost: float | None = None      # from a receipt; outranks comparables
    comparables: list[Comparable] | None = None


def load_life_table(path: str | Path | None) -> dict[str, float]:
    table = dict(DEFAULT_LIFE_YEARS)
    if path and Path(path).exists():
        table.update(json.loads(Path(path).read_text(encoding="utf-8")))
    return table


def years_between(start: date, end: date) -> float:
    return max(0.0, (end - start).days / 365.25)


def build_rows(items: list[Item], entries: dict[str, ClaimantEntry] | None = None,
               life: dict[str, float] | None = None, as_of: date | None = None) -> list[dict]:
    entries = entries or {}
    life = life or dict(DEFAULT_LIFE_YEARS)
    as_of = as_of or date.today()
    rows: list[dict] = []

    for it in items:
        flags: list[str] = []
        evidence = [d for d in it.detections if d.crop]
        if not evidence:
            # The rule, applied in code: an item nobody can see is not claimed.
            continue

        e = entries.get(it.item_id, ClaimantEntry())
        best = it.best_detection

        # Age, in order of preference: the year the claimant says they bought it
        # (or an exact receipt date), otherwise the earliest photo showing it.
        photo_year = it.earliest_date.year if it.earliest_date else None
        if e.purchase_date:
            age_exact, age_basis = years_between(e.purchase_date, as_of), f"bought {e.purchase_date.isoformat()}"
            bought_year = e.purchase_date.year
        elif e.purchase_year:
            # Whole years from the year entered: bought 2019, now 2026, 7 years.
            age_exact, age_basis = float(max(0, as_of.year - e.purchase_year)), f"bought {e.purchase_year}"
            bought_year = e.purchase_year
        elif it.earliest_date:
            age_exact, age_basis = (years_between(it.earliest_date, as_of),
                                  f"no purchase year given; seen in a photo from {it.earliest_date:%B %Y}")
            bought_year = None
        else:
            age_exact, age_basis, bought_year = None, "", None
        if bought_year and photo_year and photo_year < bought_year:
            flags.append(f"photo from {photo_year} predates stated purchase year {bought_year}")
        age = round(age_exact, 1) if age_exact is not None else None
        if age is None:
            flags.append("no date: add the year purchased")
        elif bought_year is None:
            flags.append("age from earliest photo (minimum); add the year purchased")

        sources: list[Comparable] = []
        if e.replacement_cost is not None:
            rcv, basis = e.replacement_cost, "claimant receipt or stated price"
        else:
            pr = price_from_comps(e.comparables or [])
            rcv, basis, sources = pr.replacement_cost, pr.basis, pr.sources
            flags.extend(pr.flags if (e.comparables or []) else [])
        if rcv is None:
            flags.append("replacement cost needed")

        years = life.get(it.category, FALLBACK_LIFE_YEARS)
        raw_dep = age_exact / years if age_exact is not None else None
        dep = min(MAX_DEPRECIATION, raw_dep) if raw_dep is not None else None
        capped = raw_dep is not None and raw_dep > MAX_DEPRECIATION
        acv = round(rcv * (1 - dep), 2) if (rcv is not None and dep is not None) else None
        dep_amt = round(rcv - acv, 2) if acv is not None else None

        description = e.description or best.description or it.category
        if not e.description and not best.description:
            flags.append("description is category only")

        rows.append({
            "item_id": it.item_id,
            "room": e.room,
            "description": description,
            "brand_model": e.brand_model,
            "quantity": e.quantity,
            "purchase_year": bought_year or "",
            "earliest_photo_date": it.earliest_date.isoformat() if it.earliest_date else "",
            "age_years": "" if age is None else age,
            "purchased_from": e.purchased_from,
            "replacement_cost": "" if rcv is None else f"{rcv:.2f}",
            "price_basis": basis if rcv is not None else "",
            "price_sources": " | ".join(f"{c.source} ${c.price:,.2f} {c.url}" for c in sources),
            "useful_life_years": years,
            "depreciation_pct": "" if dep is None else round(dep * 100, 1),
            "depreciation_amount": "" if dep_amt is None else f"{dep_amt:.2f}",
            "actual_cash_value": "" if acv is None else f"{acv:.2f}",
            "recoverable_depreciation": "" if dep_amt is None else f"{dep_amt:.2f}",
            "depreciation_explained": (f"{age_basis[0].upper()}{age_basis[1:]}. " if age_basis else "")
            + explain_depreciation(age_exact, years, dep, capped, rcv, acv),
            "evidence_photos": "; ".join(Path(p).name for p in it.photos),
            "evidence_crop": best.crop,
            "flags": "; ".join(flags),
        })
    return rows


def totals(rows: list[dict]) -> dict:
    rcv = sum(float(r["replacement_cost"]) * int(r["quantity"]) for r in rows if r["replacement_cost"])
    acv = sum(float(r["actual_cash_value"]) * int(r["quantity"]) for r in rows if r["actual_cash_value"])
    rec = sum(float(r["recoverable_depreciation"]) * int(r["quantity"])
              for r in rows if r["recoverable_depreciation"])
    priced = sum(1 for r in rows if r["replacement_cost"])
    return {"lines": len(rows), "priced_lines": priced, "unpriced_lines": len(rows) - priced,
            "replacement_cost_total": round(rcv, 2), "actual_cash_value_total": round(acv, 2),
            "recoverable_depreciation_total": round(rec, 2)}


def write_csv(rows: list[dict], path: str | Path, columns: list[str] | None = None,
              rename: dict[str, str] | None = None) -> str:
    """Write the schedule. `columns` and `rename` remap to a carrier's template."""
    columns = columns or COLUMNS
    rename = rename or {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([rename.get(c, c) for c in columns])
        for r in rows:
            w.writerow([r.get(c, "") for c in columns])
    return str(path)
