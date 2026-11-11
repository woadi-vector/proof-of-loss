"""Claimed versus observed: one status per schedule line.

Adapted from Punchwalk's re-walk decision table (github.com/lucianobogo/punchwalk,
MIT, punchwalk/pipeline/closures.py). Punchwalk asks whether a defect "claimed
fixed" is still visible; this asks whether an item "claimed owned and lost" is
consistent with what the walk shows.

Rules, enforced here whatever any model says:
  * Only Consistent, Not checked, Observed not claimed, and absence that carries
    no weight (theft) come back without needing attention. Everything stays
    NEEDS_REVIEW until a person signs it off.
  * A visual flag (Discrepancy or Not observed) is asked a second time, the
    other way round, before it reaches a person. If the second look disagrees,
    is unsure, or could not run, the line becomes Can't tell. No flag beats a
    wrong flag.
  * A document fact (a submitted photo dated before the stated purchase year)
    is not a visual judgement and needs no second look.
  * A room nobody walked is Not checked, never Not observed.
  * Absence after a burn-through is expected: it is Can't tell unless the walk
    recorded a positive sign the item was never there.
  * Absence after a theft is the claim itself and carries no weight.
  * Counting fewer items than claimed is a discrepancy only where items should
    still be countable (not after a burn-through, not after a theft).
"""

from __future__ import annotations

import re
from typing import Protocol

from .records import Condition, LineResult, LossType, Room, RoomDamage, ScheduleLine, Sighting, Status


class SecondLook(Protocol):
    """The reversed question. A model answers these in production; tests pass stubs.

    Each returns True, False, or None for unsure.
    """

    def matches_claim(self, line: ScheduleLine, sightings: list[Sighting]) -> bool | None:
        """Do these frames clearly show the item as the claimant described it?"""

    def seen_in_room(self, line: ScheduleLine, room: Room) -> bool | None:
        """Is there any sign of this item anywhere in this room's frames?"""


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def brand_model_difference(claimed: str, seen: str) -> str | None:
    """Plain-words difference between two brand/model strings, or None.

    Unknown on either side is never a difference.
    """
    a, b = _tokens(claimed), _tokens(seen)
    if not a or not b:
        return None
    if a[0] != b[0]:
        return f"brand: claimed {claimed!r}, seen {seen!r}"
    ma, mb = set(a[1:]), set(b[1:])
    # A model read off a label is often a prefix or variant ("QN65" vs "QN65B"):
    # only call it different when no token on one side starts the other.
    related = any(x.startswith(y) or y.startswith(x) for x in ma for y in mb)
    if ma and mb and not related:
        return f"model: claimed {claimed!r}, seen {seen!r}"
    return None


def photo_date_difference(line: ScheduleLine) -> str | None:
    if not line.purchase_year or not line.photo_dates:
        return None
    earliest = min(line.photo_dates)
    if earliest.year < line.purchase_year:
        return (f"submitted photo dated {earliest.isoformat()} predates "
                f"stated purchase year {line.purchase_year}")
    return None


def _room_key(name: str) -> str:
    return " ".join(_tokens(name))


def _quantity_countable(loss: LossType, room: Room | None) -> bool:
    if loss is LossType.THEFT:
        return False
    return not (room and room.damage is RoomDamage.BURNED_THROUGH)


def _second_look_note(answer: bool | None, ran: bool) -> str:
    if not ran:
        return "second look not run"
    return {True: "second look: matches the claim", False: "second look: does not match",
            None: "second look: unsure"}[answer]


def match_line(line: ScheduleLine, sightings: list[Sighting], room: Room | None,
               loss: LossType, checker: SecondLook | None = None,
               absence_sign: str | None = None) -> LineResult:
    base = dict(line_id=line.line_id, room=line.room)
    doc_diff = photo_date_difference(line)

    # Seen on the walk -------------------------------------------------------
    if sightings:
        clear = [s for s in sightings if s.clear]
        if not clear:
            diffs = [doc_diff] if doc_diff else []
            status = Status.DISCREPANCY if doc_diff else Status.CANT_TELL
            reason = "Seen on the walk, but no frame shows it clearly."
            if doc_diff:
                reason += " The submitted photo dates still disagree with the stated purchase year."
            return LineResult(status, reason, differences=diffs, **base)

        visual: list[str] = []
        for s in clear:
            d = brand_model_difference(line.brand_model, s.brand_model)
            if d and d not in visual:
                visual.append(d)
        if any(s.condition is Condition.UNDAMAGED for s in clear):
            visual.append("condition: recorded undamaged on the walk, claimed as a loss")
        counted = [s.quantity for s in clear if s.quantity is not None]
        if counted and sum(counted) < line.quantity:
            if _quantity_countable(loss, room):
                visual.append(f"quantity: claimed {line.quantity}, counted {sum(counted)}")

        if visual:
            ran = checker is not None
            answer = checker.matches_claim(line, clear) if ran else None
            note = _second_look_note(answer, ran)
            if ran and answer is False:
                diffs = visual + ([doc_diff] if doc_diff else [])
                return LineResult(Status.DISCREPANCY, "Seen, but it differs from the schedule.",
                                  differences=diffs, second_look=note, **base)
            # The two looks disagree or one is missing: hold it for a person.
            if doc_diff:
                return LineResult(Status.DISCREPANCY,
                                  "Visual differences unconfirmed; the photo dates still disagree.",
                                  differences=[doc_diff], second_look=note, **base)
            return LineResult(Status.CANT_TELL,
                              "A visual difference was found but not confirmed on a second look.",
                              differences=visual, second_look=note, **base)

        if doc_diff:
            return LineResult(Status.DISCREPANCY, "Seen and matches, but the photo dates disagree.",
                              differences=[doc_diff], **base)
        return LineResult(Status.CONSISTENT, "Seen on the walk and matches the schedule.",
                          needs_attention=False, **base)

    # Not seen ---------------------------------------------------------------
    if room is None or not room.walked:
        return LineResult(Status.NOT_CHECKED, "The walk did not cover this room.",
                          needs_attention=False, **base)

    if loss is LossType.THEFT:
        return LineResult(Status.NOT_OBSERVED,
                          "Not seen; absence is what a theft claim states, so it carries no weight.",
                          differences=[doc_diff] if doc_diff else [],
                          needs_attention=bool(doc_diff), **base)

    if room.damage is RoomDamage.BURNED_THROUGH and not absence_sign:
        return LineResult(Status.CANT_TELL,
                          "Not seen, but the room burned through and absence is expected.",
                          differences=[doc_diff] if doc_diff else [], **base)

    ran = checker is not None
    answer = checker.seen_in_room(line, room) if ran else None
    note = ("second look not run" if not ran else
            {True: "second look: a sign of it was found", False: "second look: no sign of it",
             None: "second look: unsure"}[answer])
    if ran and answer is False:
        reason = "Room walked; the item was not seen."
        if absence_sign:
            reason += f" Recorded sign: {absence_sign}."
        diffs = ["not seen in a walked room"] + ([doc_diff] if doc_diff else [])
        return LineResult(Status.NOT_OBSERVED, reason, differences=diffs, second_look=note, **base)
    return LineResult(Status.CANT_TELL, "Not seen at first, and the second look did not confirm it.",
                      differences=[doc_diff] if doc_diff else [], second_look=note, **base)


def match_schedule(schedule: list[ScheduleLine], sightings: list[Sighting], rooms: list[Room],
                   loss: LossType, checker: SecondLook | None = None,
                   absence_signs: dict[str, str] | None = None) -> list[LineResult]:
    """One result per schedule line, then one per sighting that matches no line."""
    absence_signs = absence_signs or {}
    room_by_key = {_room_key(r.name): r for r in rooms}
    known = {ln.line_id for ln in schedule}
    by_line: dict[str, list[Sighting]] = {}
    for s in sightings:
        if s.line_id in known:
            by_line.setdefault(s.line_id, []).append(s)

    results = [match_line(ln, by_line.get(ln.line_id, []), room_by_key.get(_room_key(ln.room)),
                          loss, checker, absence_signs.get(ln.line_id))
               for ln in schedule]

    for s in sightings:
        if s.line_id in known:
            continue
        reason = "Seen on the walk; no schedule line claims it."
        if s.line_id is not None:
            reason += f" (matched to unknown line {s.line_id!r})"
        results.append(LineResult(Status.OBSERVED_NOT_CLAIMED, reason, sighting_id=s.sighting_id,
                                  room=s.room, needs_attention=False))
    return results
