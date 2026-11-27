"""The cloud check: one claim in as JSON, the checked and priced result out.

This is the code that runs as the Firebase Cloud Function (functions/main.py).
It makes no model calls. The phone does the looking: Gemini Nano on the device
spots items, judges whether a frame is clear, and answers the reversed second
look; cloud Gemini reads the narration and finds cited listings. Their answers
arrive here as plain fields, and this module applies the rules that decide
status, price, depreciation and betterment. Models observe; code decides.

Input (all keys optional except schedule):
  loss_type        "fire" | "theft" | "water" | "other"
  as_of            ISO date the claim is valued on (default: today)
  schedule         sworn schedule lines (ScheduleLine fields)
  sightings        what the walk saw (Sighting fields)
  rooms            rooms on the walk (Room fields)
  second_looks     {"<line_id>": true | false | null}, the reversed question
                   answered on the device for lines with a visual difference
                   or no sighting; a line not listed counts as "not run"
  absence_signs    {"<line_id>": "what the walk recorded"}
  comps            {"<line_id>": [Comparable fields]}, cited listings for the
                   claimed item
  observed_comps   {"<line_id>": [Comparable fields]}, cited listings for the
                   item the walk actually saw (like kind and quality)
  receipts         {"<line_id>": price}
  accepted         [line_id, ...] lines a person accepted in review
  life_years       {"<category>": years} overrides the useful life table

Output: results (one per line, plus unclaimed sightings), valuations,
like kind and quality findings, the documented value total, and counts.
Every result keeps review_status NEEDS_REVIEW.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import date
from enum import Enum
from typing import Any

from .lkq import apply_lkq, like_kind_check
from .matcher import match_schedule
from .pricing import Comparable
from .records import (Condition, LineResult, LossType, Room, RoomDamage, ScheduleLine,
                      Sighting, Status)
from .valuation import documented_value, value_line


class InputError(ValueError):
    """The request is malformed; the message says which field and why."""


def _pick(cls, raw: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise InputError(f"{where}: expected an object")
    names = {f.name for f in fields(cls)}
    unknown = sorted(set(raw) - names)
    if unknown:
        raise InputError(f"{where}: unknown field(s) {', '.join(unknown)}")
    return dict(raw)


def _enum(enum_cls, value: Any, where: str):
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(e.value for e in enum_cls)
        raise InputError(f"{where}: {value!r} is not one of {allowed}") from None


def _date(value: Any, where: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise InputError(f"{where}: {value!r} is not an ISO date (YYYY-MM-DD)") from None


def _line(raw: dict, i: int) -> ScheduleLine:
    d = _pick(ScheduleLine, raw, f"schedule[{i}]")
    for key in ("line_id", "room", "description"):
        if not d.get(key):
            raise InputError(f"schedule[{i}]: {key} is required")
    d["photo_dates"] = [_date(v, f"schedule[{i}].photo_dates") for v in d.get("photo_dates", [])]
    return ScheduleLine(**d)


def _sighting(raw: dict, i: int) -> Sighting:
    d = _pick(Sighting, raw, f"sightings[{i}]")
    for key in ("sighting_id", "room", "description"):
        if not d.get(key):
            raise InputError(f"sightings[{i}]: {key} is required")
    if "condition" in d:
        d["condition"] = _enum(Condition, d["condition"], f"sightings[{i}].condition")
    return Sighting(**d)


def _room(raw: dict, i: int) -> Room:
    d = _pick(Room, raw, f"rooms[{i}]")
    if not d.get("name"):
        raise InputError(f"rooms[{i}]: name is required")
    if "damage" in d:
        d["damage"] = _enum(RoomDamage, d["damage"], f"rooms[{i}].damage")
    return Room(**d)


def _comps(raw: Any, where: str) -> list[Comparable]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InputError(f"{where}: expected a list of listings")
    return [Comparable(**_pick(Comparable, c, f"{where}[{j}]")) for j, c in enumerate(raw)]


class DeviceSecondLook:
    """The reversed question, answered earlier on the phone and passed in.

    The same answer serves both questions the matcher can ask about a line
    ("do these frames match the claim?" and "is there any sign of it in the
    room?"), because the device asks whichever one applies to that line.
    """

    def __init__(self, answers: dict[str, bool | None]):
        self.answers = answers

    def _get(self, line_id: str) -> bool | None:
        return self.answers.get(line_id)

    def matches_claim(self, line: ScheduleLine, sightings: list[Sighting]) -> bool | None:
        return self._get(line.line_id)

    def seen_in_room(self, line: ScheduleLine, room: Room) -> bool | None:
        return self._get(line.line_id)


def _plain(obj: Any) -> Any:
    """Dataclasses, enums and dates to JSON-ready values."""
    if is_dataclass(obj):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def check_claim(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise InputError("request body must be a JSON object")
    raw_schedule = payload.get("schedule")
    if not isinstance(raw_schedule, list) or not raw_schedule:
        raise InputError("schedule: at least one line is required")

    loss = _enum(LossType, payload.get("loss_type", "other"), "loss_type")
    as_of = _date(payload["as_of"], "as_of") if payload.get("as_of") else date.today()
    schedule = [_line(r, i) for i, r in enumerate(raw_schedule)]
    sightings = [_sighting(r, i) for i, r in enumerate(payload.get("sightings") or [])]
    rooms = [_room(r, i) for i, r in enumerate(payload.get("rooms") or [])]

    ids = [ln.line_id for ln in schedule]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        raise InputError(f"schedule: duplicate line_id {', '.join(dupes)}")

    second = payload.get("second_looks")
    checker = None
    if second is not None:
        if not isinstance(second, dict) or any(v not in (True, False, None) for v in second.values()):
            raise InputError("second_looks: expected {line_id: true | false | null}")
        checker = DeviceSecondLook(second)

    results = match_schedule(schedule, sightings, rooms, loss, checker,
                             payload.get("absence_signs") or {})

    comps = payload.get("comps") or {}
    receipts = payload.get("receipts") or {}
    life = payload.get("life_years") or None
    valuations = {}
    for ln in schedule:
        valuations[ln.line_id] = value_line(
            ln, _comps(comps.get(ln.line_id), f"comps.{ln.line_id}"),
            receipt=receipts.get(ln.line_id), life=life, as_of=as_of)

    # Like kind and quality: only for lines the walk saw clearly and that have
    # cited listings for the observed item.
    observed = payload.get("observed_comps") or {}
    by_line = {ln.line_id: ln for ln in schedule}
    findings = []
    for line_id, raw in observed.items():
        line = by_line.get(line_id)
        if line is None:
            raise InputError(f"observed_comps: no schedule line {line_id!r}")
        seen = [s for s in sightings if s.line_id == line_id and s.clear]
        if not seen:
            continue
        findings.append(like_kind_check(line, seen[0], _comps(raw, f"observed_comps.{line_id}")))
    apply_lkq(results, findings)

    accepted = set(payload.get("accepted") or [])
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1

    return {
        "as_of": as_of.isoformat(),
        "loss_type": loss.value,
        "results": _plain(results),
        "valuations": _plain(valuations),
        "like_kind_and_quality": _plain(findings),
        "documented_value": documented_value(results, valuations, accepted),
        "counts": counts,
        "needs_attention": sum(1 for r in results if r.needs_attention),
    }
