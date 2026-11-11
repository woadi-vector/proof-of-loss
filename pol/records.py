"""The records the contents module works on.

A claim arrives as a sworn schedule (what the claimant says was owned and lost).
A walk produces sightings (what the investigator saw and said, with the frame
that shows it) and a list of rooms with how far each was walked and damaged.
The matcher turns those into one LineResult per schedule line, plus one per
sighting that matches no line.

Nothing here decides fraud. Every result starts as NEEDS_REVIEW, the same
default Clarice's scene reconstruction uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class LossType(str, Enum):
    FIRE = "fire"
    THEFT = "theft"
    WATER = "water"
    OTHER = "other"


class RoomDamage(str, Enum):
    """How the investigator tagged a room on the walk."""
    NONE = "none"
    LIGHT = "light"                    # smoke, soot, light water
    BURNED_THROUGH = "burned_through"  # contents largely consumed
    FLOODED = "flooded"


class Condition(str, Enum):
    """Condition the adjuster recorded on the first walk."""
    DESTROYED = "destroyed"
    DAMAGED = "damaged"
    UNDAMAGED = "undamaged"
    UNKNOWN = "unknown"


class Status(str, Enum):
    CONSISTENT = "consistent"
    DISCREPANCY = "discrepancy"
    CANT_TELL = "cant_tell"
    NOT_OBSERVED = "not_observed"
    NOT_CHECKED = "not_checked"
    OBSERVED_NOT_CLAIMED = "observed_not_claimed"


REVIEW_STATUS = "NEEDS_REVIEW"


@dataclass
class ScheduleLine:
    """One row of the claimant's sworn contents schedule."""
    line_id: str
    room: str
    description: str
    category: str = ""               # e.g. "television"; drives useful life
    brand_model: str = ""
    quantity: int = 1
    purchase_year: int | None = None
    claimed_amount: float | None = None   # per unit, as claimed
    photo_dates: list[date] = field(default_factory=list)  # EXIF dates of photos the claimant submitted


@dataclass
class Sighting:
    """One item the investigator saw and named on the walk.

    `line_id` is the schedule line the mention was matched to, or None when it
    matches nothing on the schedule. Matching itself happens upstream (a model
    reads the quote and the frame); this record carries its answer.
    """
    sighting_id: str
    room: str
    description: str
    line_id: str | None = None
    brand_model: str = ""            # as read or said on the walk; "" when unknown
    quantity: int | None = None      # None when not counted
    clear: bool = True               # the evidence frame clearly shows the item
    remains: bool = False            # identifiable remains, not an intact item
    condition: Condition = Condition.UNKNOWN
    frame: str = ""                  # path or media id of the evidence frame
    timestamp_s: float | None = None # second in the walk video
    quote: str = ""                  # the investigator's own words


@dataclass
class Room:
    name: str
    walked: bool = True
    damage: RoomDamage = RoomDamage.NONE


@dataclass
class LineResult:
    """What the module returns for one schedule line or one unclaimed sighting."""
    status: Status
    reason: str
    line_id: str | None = None
    sighting_id: str | None = None
    room: str = ""
    differences: list[str] = field(default_factory=list)  # which fields disagree, in plain words
    needs_attention: bool = True     # False only for Consistent and for absence that carries no weight
    second_look: str = ""            # what the reversed question answered, when asked
    review_status: str = REVIEW_STATUS
