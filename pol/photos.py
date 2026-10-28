"""Photo dates and object crops."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PIL import Image

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME = 306
_EXIF_IFD = 0x8769


def photo_date(path: str | Path) -> date | None:
    """When the photo was taken, from EXIF. None if the file carries no date.

    File modification time is deliberately not used as a fallback: copying a
    photo from a phone resets it, and a wrong date would make an old couch look
    new, which costs the claimant money in depreciation.
    """
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL) or exif.get(_EXIF_DATETIME)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S").date()
    except ValueError:
        return None


def crop(photo: str | Path, box: tuple[float, float, float, float], out: str | Path,
         pad: float = 0.04) -> str:
    """Save the object's region of the photo, with a little margin, and return its path."""
    with Image.open(photo) as im:
        im = im.convert("RGB")
        w, h = im.size
        l, t, r, b = box
        px, py = (r - l) * pad, (b - t) * pad
        region = (max(0, int(l - px)), max(0, int(t - py)),
                  min(w, int(r + px)), min(h, int(b + py)))
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        im.crop(region).save(out, quality=90)
    return str(out)
