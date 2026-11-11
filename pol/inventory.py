"""Document mode: the carrier's own contents record from the first walk.

The first walk usually comes before the claimant files a sworn proof of loss.
So there is no schedule to test yet; instead every item the adjuster names
becomes an InventoryRecord: room, description, condition, evidence, and a
priced, depreciated value. When the schedule arrives, check mode compares it
against these records, so the carrier's record predates the list it tests.

Rules:
  * no evidence, no record: an item without a frame is not recorded
  * the adjuster's stated age ("about five years old") sets the age; it is
    marked as an estimate, never as a purchase year the claimant gave
  * pricing and depreciation are the same code check mode uses (valuation.py)
  * assigning records to schedule lines is proposed, never forced: the
    baseline here only pairs records it can pair one to one; anything
    ambiguous is left for the model or a person, and stays visible
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .pricing import Comparable
from .records import Condition, ScheduleLine, Sighting
from .valuation import Valuation, value_line


@dataclass
class Observation:
    """One item the adjuster named on the first walk, as the pipeline extracted it."""
    observation_id: str
    room: str
    description: str
    category: str = ""
    brand_model: str = ""
    quantity: int = 1
    condition: Condition = Condition.UNKNOWN
    stated_age_years: float | None = None   # adjuster's estimate, if said
    frame: str = ""
    timestamp_s: float | None = None
    quote: str = ""
    clear: bool = True
    remains: bool = False


@dataclass
class InventoryRecord:
    record_id: str
    observation: Observation
    valuation: Valuation
    flags: list[str] = field(default_factory=list)

    @property
    def room(self) -> str:
        return self.observation.room


def _as_line(obs: Observation, as_of: date) -> ScheduleLine:
    year = None
    if obs.stated_age_years is not None:
        year = as_of.year - int(round(obs.stated_age_years))
    return ScheduleLine(line_id=obs.observation_id, room=obs.room, description=obs.description,
                        category=obs.category, brand_model=obs.brand_model,
                        quantity=obs.quantity, purchase_year=year)


def build_inventory(observations: list[Observation],
                    comps: dict[str, list[Comparable]] | None = None,
                    life: dict[str, float] | None = None,
                    as_of: date | None = None) -> list[InventoryRecord]:
    as_of = as_of or date.today()
    comps = comps or {}
    records: list[InventoryRecord] = []
    for obs in observations:
        if not obs.frame:
            continue  # no evidence, no record
        note = (f"Estimated {obs.stated_age_years:g} years old by the adjuster on the walk."
                if obs.stated_age_years is not None else None)
        val = value_line(_as_line(obs, as_of), comps.get(obs.observation_id), life=life,
                         as_of=as_of, age_note=note)
        flags = list(val.flags)
        if obs.stated_age_years is not None:
            flags.append("age is the adjuster's estimate on the walk")
        if not obs.clear:
            flags.append("frame does not show the item clearly")
        records.append(InventoryRecord(f"rec-{len(records) + 1:03d}", obs, val, flags))
    return records


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def baseline_assignment(schedule: list[ScheduleLine],
                        records: list[InventoryRecord]) -> dict[str, str]:
    """Pair records with schedule lines only where the pairing is one to one.

    A record and a line pair when they share room and category, and no other
    record or line in that room shares the category. Everything else is left
    unassigned for a model or a person; an unassigned record shows up as
    Observed not claimed, an unassigned line goes through the not-seen rules.
    """
    buckets_l: dict[tuple[str, str], list[ScheduleLine]] = {}
    buckets_r: dict[tuple[str, str], list[InventoryRecord]] = {}
    for ln in schedule:
        if ln.category:
            buckets_l.setdefault((_norm(ln.room), _norm(ln.category)), []).append(ln)
    for r in records:
        if r.observation.category:
            buckets_r.setdefault((_norm(r.room), _norm(r.observation.category)), []).append(r)
    out: dict[str, str] = {}
    for key, lines in buckets_l.items():
        recs = buckets_r.get(key, [])
        if len(lines) == 1 and len(recs) == 1:
            out[recs[0].record_id] = lines[0].line_id
    return out


def to_sightings(records: list[InventoryRecord], assignment: dict[str, str]) -> list[Sighting]:
    """Turn first-walk records into the sightings check mode reads."""
    out = []
    for r in records:
        o = r.observation
        out.append(Sighting(sighting_id=r.record_id, room=o.room, description=o.description,
                            line_id=assignment.get(r.record_id), brand_model=o.brand_model,
                            quantity=o.quantity, clear=o.clear, remains=o.remains,
                            condition=o.condition, frame=o.frame, timestamp_s=o.timestamp_s,
                            quote=o.quote))
    return out


def totals(records: list[InventoryRecord]) -> dict:
    rcv = sum(r.valuation.documented_total or 0 for r in records)
    acv = sum((r.valuation.actual_cash_value or 0) * r.valuation.quantity for r in records)
    unpriced = sum(1 for r in records if r.valuation.documented_total is None)
    by_condition: dict[str, int] = {}
    for r in records:
        c = r.observation.condition.value
        by_condition[c] = by_condition.get(c, 0) + 1
    return {"records": len(records), "replacementCost": round(rcv, 2), "actualCashValue": round(acv, 2),
            "unpriced": unpriced, "byCondition": by_condition}
