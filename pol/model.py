"""Core records: what was seen in a photo, and what a merged item is."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Detection:
    """One object SAM 3.1 found in one photo."""

    det_id: str               # unique across the whole run, e.g. "IMG_1234:television:0"
    photo: str                # path to the source photo
    photo_date: date | None   # when the photo was taken (EXIF), None if unknown
    category: str             # the prompt that found it, e.g. "television"
    box: tuple[float, float, float, float]  # left, top, right, bottom in source pixels
    confidence: float | None = None
    crop: str | None = None   # path to the cropped image of just this object
    description: str = ""     # filled later by Muse Spark; empty until then

    @property
    def area(self) -> float:
        l, t, r, b = self.box
        return max(0.0, r - l) * max(0.0, b - t)

    @property
    def aspect(self) -> float:
        l, t, r, b = self.box
        h = max(1.0, b - t)
        return max(1.0, r - l) / h


@dataclass
class Item:
    """One real-world belonging, possibly seen in many photos."""

    item_id: str
    category: str
    detections: list[Detection] = field(default_factory=list)
    # How each merge was decided, so the claimant (and a desk adjuster) can see why
    # two photos were treated as the same object.
    merge_notes: list[str] = field(default_factory=list)

    @property
    def photos(self) -> list[str]:
        return sorted({d.photo for d in self.detections})

    @property
    def earliest_date(self) -> date | None:
        dates = [d.photo_date for d in self.detections if d.photo_date]
        return min(dates) if dates else None

    @property
    def best_detection(self) -> Detection:
        """The clearest view: highest confidence, then largest on screen."""
        return max(self.detections, key=lambda d: ((d.confidence or 0.0), d.area))
