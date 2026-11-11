"""Like kind and quality: is the claimed replacement better than what was there?

A claims manager trusts this flag because it rests on two checkable things:
the frame showing the item that was actually in the room, and cited listings
for that observed item. The claimed replacement cost is compared against the
like kind and quality price of the observed model, and the difference is shown
as dollars (the betterment).

Rules:
  * the observed item is priced the same way as everything else: cited
    listings, averaged in pricing.py; a model may find listings, never the number
  * no clear frame, no comparison; no cited listings, no comparison
  * a difference counts only past a tolerance (default 25% over the observed
    price) and a dollar floor (default $50 total), so small pricing noise is
    never raised
  * the finding states numbers and evidence, never a motive
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import Comparable, price_from_comps
from .records import LineResult, ScheduleLine, Sighting, Status

LKQ_TOLERANCE = 0.25
LKQ_MIN_DOLLARS = 50.0


@dataclass
class LKQFinding:
    line_id: str
    observed_item: str
    observed_replacement: float | None      # per unit, like kind and quality for what was seen
    claimed_replacement: float | None       # per unit, as claimed
    betterment_per_unit: float | None
    betterment_total: float | None
    exceeds: bool                           # past tolerance and dollar floor
    explained: str
    sources: list[Comparable] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(int(seconds), 60)
    return f", at {m}:{s:02d} in the walk"


def _money(x: float) -> str:
    return f"${x:,.2f}"


def like_kind_check(line: ScheduleLine, sighting: Sighting,
                    observed_comps: list[Comparable] | None,
                    tolerance: float = LKQ_TOLERANCE,
                    min_dollars: float = LKQ_MIN_DOLLARS) -> LKQFinding:
    observed = sighting.brand_model or sighting.description
    seen = f"{observed}{_clock(sighting.timestamp_s)}"

    def none(why: str, flags: list[str] | None = None, rcv: float | None = None,
             sources: list[Comparable] | None = None) -> LKQFinding:
        return LKQFinding(line.line_id, observed, rcv, line.claimed_amount, None, None, False,
                          why, sources or [], flags or [])

    if not sighting.clear:
        return none("The frame does not show the item clearly enough to identify it; "
                    "no like kind and quality comparison.")
    if not observed:
        return none("The observed item was not identified; no like kind and quality comparison.")

    pr = price_from_comps(observed_comps or [])
    if pr.replacement_cost is None:
        return none(f"No cited listings for the observed item ({observed}); "
                    "no like kind and quality comparison.", pr.flags)
    rcv = pr.replacement_cost
    if line.claimed_amount is None:
        return none(f"Like kind and quality for the observed item ({seen}) is {_money(rcv)}; "
                    "the schedule gives no claimed amount to compare.", pr.flags, rcv, pr.sources)

    per_unit = round(line.claimed_amount - rcv, 2)
    total = round(per_unit * line.quantity, 2)
    exceeds = per_unit > rcv * tolerance and total >= min_dollars
    n = len(pr.sources)
    text = (f"Claimed replacement {_money(line.claimed_amount)} per unit. Like kind and quality for "
            f"the observed item ({seen}) averages {_money(rcv)} across {n} cited "
            f"listing{'s' if n != 1 else ''}.")
    if exceeds:
        text += f" Difference {_money(per_unit)} per unit, {_money(total)} total."
    elif per_unit > 0:
        text += f" Difference {_money(per_unit)} per unit, within tolerance."
    else:
        text += " The claimed amount does not exceed it."
    return LKQFinding(line.line_id, observed, rcv, line.claimed_amount, per_unit, total, exceeds,
                      text, pr.sources, pr.flags)


def apply_lkq(results: list[LineResult], findings: list[LKQFinding]) -> None:
    """Add exceeding findings to the matcher's results, in place.

    A Consistent line becomes a Discrepancy (the item was there, but the claimed
    replacement is better than what was there). Any other status keeps its
    status and gains the betterment as a stated difference.
    """
    by_line = {f.line_id: f for f in findings if f.exceeds}
    for r in results:
        f = by_line.get(r.line_id) if r.line_id else None
        if not f:
            continue
        r.differences.append(f"like kind and quality: {f.explained}")
        r.needs_attention = True
        if r.status is Status.CONSISTENT:
            r.status = Status.DISCREPANCY
            r.reason = "Seen, but the claimed replacement is better than the item observed."
