"""Decide when detections in different photos are the same belonging.

This is the step that makes a camera roll worth more than memory: the same
couch shows up in a birthday photo from 2019 and a holiday photo from 2023,
in two different apartments, and it must become ONE line on the claim, dated
from the earliest photo.

Three outcomes for every candidate pair:
  merge   - code is confident they are the same object
  review  - plausible; a vision model (Muse Spark) makes the call
  separate- different objects

Hard rules, enforced in code regardless of any score or model answer:
  * two detections from the SAME photo are never the same item (two lamps in
    one picture are two lamps)
  * different categories are never merged
  * an item never absorbs a group that already has a detection from one of its
    photos (prevents chaining A=B, B=C into A=C across a shared photo)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PIL import Image

from .model import Detection, Item

# Thresholds on the combined similarity score (0 to 1). Tuned on real photos
# later; these are starting points.
MERGE_AT = 0.82
REVIEW_AT = 0.60

# Within one photo, two prompts can find the same object ("couch" and "sofa").
SAME_PHOTO_IOU = 0.6


@dataclass
class PairDecision:
    a: str
    b: str
    score: float
    outcome: str          # "merge" | "review" | "separate"
    reason: str


def color_features(path: str) -> tuple[float, float, float]:
    """Mean chromaticity (r, g share of brightness) and mean luminance of a crop.

    Chromaticity is used because it barely moves when the same couch is shot in
    a dimmer room; luminance is compared separately and tolerantly. Fixed-bin
    histograms were tried first and failed exactly that case: a 7% dimming
    pushed grey upholstery across a bin edge and scored the same couch as a
    stranger.
    """
    with Image.open(path) as im:
        im = im.convert("RGB").resize((64, 64))
        n = r_sum = g_sum = b_sum = 0
        for r, g, b in im.getdata():
            r_sum += r; g_sum += g; b_sum += b; n += 1
    total = (r_sum + g_sum + b_sum) or 1
    lum = (0.299 * r_sum + 0.587 * g_sum + 0.114 * b_sum) / max(1, n)
    return r_sum / total, g_sum / total, lum


CHROMA_TOLERANCE = 0.08   # chromaticity distance at which colors count as unrelated


def color_similarity(f1: tuple[float, float, float], f2: tuple[float, float, float]) -> float:
    """1.0 same color and brightness, 0.0 unrelated colors."""
    chroma = ((f1[0] - f2[0]) ** 2 + (f1[1] - f2[1]) ** 2) ** 0.5
    chroma_sim = max(0.0, 1.0 - chroma / CHROMA_TOLERANCE)
    lum_sim = min(f1[2], f2[2]) / max(f1[2], f2[2], 1e-6)
    return 0.7 * chroma_sim + 0.3 * lum_sim


def shape_similarity(a: Detection, b: Detection) -> float:
    """Aspect ratio agreement. A couch is wide in every photo; size is not compared
    because distance from the camera changes it."""
    ra, rb = a.aspect, b.aspect
    return min(ra, rb) / max(ra, rb)


def iou(a: Detection, b: Detection) -> float:
    al, at, ar, ab = a.box
    bl, bt, br, bb = b.box
    iw = max(0.0, min(ar, br) - max(al, bl))
    ih = max(0.0, min(ab, bb) - max(at, bt))
    inter = iw * ih
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def dedupe_within_photo(dets: list[Detection]) -> tuple[list[Detection], list[str]]:
    """Drop the weaker of two overlapping detections in the same photo."""
    keep: list[Detection] = []
    notes: list[str] = []
    for d in sorted(dets, key=lambda x: -(x.confidence or 0.0)):
        clash = next((k for k in keep if k.photo == d.photo and iou(k, d) >= SAME_PHOTO_IOU), None)
        if clash:
            notes.append(f"{d.det_id} overlaps {clash.det_id} in the same photo; kept the stronger one")
        else:
            keep.append(d)
    return keep, notes


def score_pair(a: Detection, b: Detection, hist: dict[str, tuple[float, float, float]]) -> float:
    color = color_similarity(hist[a.det_id], hist[b.det_id]) if a.crop and b.crop else 0.0
    return 0.75 * color + 0.25 * shape_similarity(a, b)


Judge = Callable[[Detection, Detection], "bool | None"]


def merge(dets: list[Detection], judge: Judge | None = None) -> tuple[list[Item], list[PairDecision]]:
    """Group detections into items. `judge` answers review pairs: True same,
    False different, None unsure. Unsure pairs stay separate, because listing a
    couch twice is caught by the claimant in review, while silently merging two
    different couches loses one from the claim."""
    dets, _ = dedupe_within_photo(dets)
    hist = {d.det_id: color_features(d.crop) for d in dets if d.crop}

    decisions: list[PairDecision] = []
    for i, a in enumerate(dets):
        for b in dets[i + 1:]:
            if a.category != b.category:
                continue
            if a.photo == b.photo:
                decisions.append(PairDecision(a.det_id, b.det_id, 0.0, "separate", "same photo"))
                continue
            s = score_pair(a, b, hist)
            if s >= MERGE_AT:
                decisions.append(PairDecision(a.det_id, b.det_id, s, "merge", f"score {s:.2f}"))
            elif s >= REVIEW_AT:
                verdict = judge(a, b) if judge else None
                if verdict is True:
                    decisions.append(PairDecision(a.det_id, b.det_id, s, "merge", f"score {s:.2f}, model: same"))
                elif verdict is False:
                    decisions.append(PairDecision(a.det_id, b.det_id, s, "separate", f"score {s:.2f}, model: different"))
                else:
                    decisions.append(PairDecision(a.det_id, b.det_id, s, "review", f"score {s:.2f}, undecided"))
            else:
                decisions.append(PairDecision(a.det_id, b.det_id, s, "separate", f"score {s:.2f}"))

    # Union strongest pairs first, refusing any union that would put two
    # detections from one photo into the same item.
    group = {d.det_id: {d.det_id} for d in dets}
    photo_of = {d.det_id: d.photo for d in dets}
    for p in sorted((p for p in decisions if p.outcome == "merge"), key=lambda p: -p.score):
        ga, gb = group[p.a], group[p.b]
        if ga is gb:
            continue
        if {photo_of[x] for x in ga} & {photo_of[x] for x in gb}:
            p.outcome, p.reason = "separate", p.reason + "; blocked, would join two objects from one photo"
            continue
        joined = ga | gb
        for x in joined:
            group[x] = joined

    by_id = {d.det_id: d for d in dets}
    items: list[Item] = []
    seen: set[int] = set()
    for g in group.values():
        if id(g) in seen:
            continue
        seen.add(id(g))
        members = sorted((by_id[x] for x in g), key=lambda d: (d.photo_date is None, d.photo_date, d.det_id))
        notes = [f"{p.a} = {p.b} ({p.reason})" for p in decisions
                 if p.outcome == "merge" and p.a in g and p.b in g]
        items.append(Item(item_id=f"item-{len(items) + 1:03d}", category=members[0].category,
                          detections=members, merge_notes=notes))
    items.sort(key=lambda it: (it.category, it.item_id))
    for n, it in enumerate(items, 1):
        it.item_id = f"item-{n:03d}"
    return items, decisions
