"""The schedule of loss: the document the claimant actually files.

Rules this module enforces, whatever any model says:
  * no evidence, no line: every row cites at least one photo and crop
  * the model never sets a price: replacement cost comes from the claimant, or
    is left blank and flagged
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
    "earliest_photo_date", "age_years", "purchased_from",
    "replacement_cost", "depreciation_pct", "actual_cash_value",
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
    purchase_date: date | None = None
    replacement_cost: float | None = None


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

        # Age: the claimant's purchase date if given, otherwise the earliest photo.
        if e.purchase_date:
            start, age_basis = e.purchase_date, "purchase date"
        elif it.earliest_date:
            start, age_basis = it.earliest_date, "earliest photo"
        else:
            start, age_basis = None, ""
        if e.purchase_date and it.earliest_date and it.earliest_date < e.purchase_date:
            flags.append("photo predates stated purchase date")
        age_exact = years_between(start, as_of) if start else None
        age = round(age_exact, 1) if age_exact is not None else None
        if age is None:
            flags.append("no date: add purchase date")
        elif age_basis == "earliest photo":
            flags.append("age from earliest photo (minimum)")

        rcv = e.replacement_cost
        if rcv is None:
            flags.append("replacement cost needed")

        years = life.get(it.category, FALLBACK_LIFE_YEARS)
        dep = min(MAX_DEPRECIATION, age_exact / years) if age_exact is not None else None
        acv = round(rcv * (1 - dep), 2) if (rcv is not None and dep is not None) else None

        description = e.description or best.description or it.category
        if not e.description and not best.description:
            flags.append("description is category only")

        rows.append({
            "item_id": it.item_id,
            "room": e.room,
            "description": description,
            "brand_model": e.brand_model,
            "quantity": e.quantity,
            "earliest_photo_date": it.earliest_date.isoformat() if it.earliest_date else "",
            "age_years": "" if age is None else age,
            "purchased_from": e.purchased_from,
            "replacement_cost": "" if rcv is None else f"{rcv:.2f}",
            "depreciation_pct": "" if dep is None else round(dep * 100, 1),
            "actual_cash_value": "" if acv is None else f"{acv:.2f}",
            "evidence_photos": "; ".join(Path(p).name for p in it.photos),
            "evidence_crop": best.crop,
            "flags": "; ".join(flags),
        })
    return rows


def totals(rows: list[dict]) -> dict:
    rcv = sum(float(r["replacement_cost"]) * int(r["quantity"]) for r in rows if r["replacement_cost"])
    acv = sum(float(r["actual_cash_value"]) * int(r["quantity"]) for r in rows if r["actual_cash_value"])
    priced = sum(1 for r in rows if r["replacement_cost"])
    return {"lines": len(rows), "priced_lines": priced, "unpriced_lines": len(rows) - priced,
            "replacement_cost_total": round(rcv, 2), "actual_cash_value_total": round(acv, 2)}


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
