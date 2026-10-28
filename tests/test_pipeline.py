"""Merge and schedule tests on synthetic crops. No API calls.

Run from the repo folder:  python tests/test_pipeline.py
"""

from __future__ import annotations

import random
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from pol.merge import merge  # noqa: E402
from pol.model import Detection  # noqa: E402
from pol.schedule import ClaimantEntry, build_rows, totals, write_csv  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pol_test_"))


def fake_crop(name: str, rgb: tuple[int, int, int], size=(160, 80), brightness=1.0, seed=0) -> str:
    """A textured patch of one dominant color, like a crop of upholstery or a TV."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    px = im.load()
    for x in range(size[0]):
        for y in range(size[1]):
            n = rnd.randint(-12, 12)
            px[x, y] = tuple(max(0, min(255, int((c + n) * brightness))) for c in rgb)
    path = TMP / f"{name}.jpg"
    im.save(path)
    return str(path)


def det(det_id, photo, when, cat, box, color, brightness=1.0, conf=0.9, seed=0):
    w, h = int(box[2] - box[0]), int(box[3] - box[1])
    return Detection(det_id=det_id, photo=photo, photo_date=when, category=cat, box=box,
                     confidence=conf, crop=fake_crop(det_id, color, (w // 4, h // 4), brightness, seed))


GREY_COUCH = (120, 124, 130)
BROWN_COUCH = (110, 70, 40)
TV_BLACK = (20, 20, 24)
LAMP_TAN = (200, 180, 140)


def scenario():
    return [
        # 2019, first apartment: the grey couch and the TV
        det("p1:couch:0", "home1_2019.jpg", date(2019, 3, 2), "couch", (0, 400, 1200, 900), GREY_COUCH, seed=1),
        det("p1:tv:0", "home1_2019.jpg", date(2019, 3, 2), "television", (300, 100, 900, 450), TV_BLACK, seed=2),
        # 2023, second home, dimmer room: the SAME grey couch, plus a new brown one
        det("p2:couch:0", "home2_2023.jpg", date(2023, 12, 25), "couch", (100, 380, 1250, 880), GREY_COUCH,
            brightness=0.93, seed=3),
        det("p2:couch:1", "home2_2023.jpg", date(2023, 12, 25), "couch", (1300, 420, 2100, 900), BROWN_COUCH, seed=4),
        # 2024: two matching lamps in one photo must stay two lamps
        det("p3:lamp:0", "home2_2024.jpg", date(2024, 6, 1), "lamp", (100, 100, 220, 400), LAMP_TAN, seed=5),
        det("p3:lamp:1", "home2_2024.jpg", date(2024, 6, 1), "lamp", (900, 100, 1020, 400), LAMP_TAN, seed=6),
        # same photo, a second prompt found the TV again with near-identical box
        det("p1:tv:dup", "home1_2019.jpg", date(2019, 3, 2), "television", (305, 105, 895, 445), TV_BLACK,
            conf=0.7, seed=7),
    ]


def test_merge():
    items, decisions = merge(scenario())
    by_cat: dict[str, list] = {}
    for it in items:
        by_cat.setdefault(it.category, []).append(it)

    couches = by_cat["couch"]
    assert len(couches) == 2, f"expected grey + brown couch, got {len(couches)}"
    grey = next(c for c in couches if len(c.detections) == 2)
    assert grey.earliest_date == date(2019, 3, 2), "grey couch should date from the 2019 photo"
    assert {d.det_id for d in grey.detections} == {"p1:couch:0", "p2:couch:0"}

    assert len(by_cat["lamp"]) == 2, "two lamps in one photo must stay two items"
    assert len(by_cat["television"]) == 1, "duplicate TV detection in one photo should collapse"
    print(f"  merge ok: {len(items)} items from {len(scenario())} detections")
    for p in decisions:
        if p.outcome != "separate" or "same photo" not in p.reason:
            print(f"    {p.a:12s} vs {p.b:12s} {p.outcome:8s} {p.reason}")
    return items


def test_judge_unsure_stays_separate():
    # A blue-grey couch is close enough to the grey one to be plausible but not
    # certain: the review band. Unsure must never merge.
    dets = scenario()[:3]
    dets[2] = det("p2:couch:0", "home2_2023.jpg", date(2023, 12, 25), "couch", (100, 380, 1250, 880),
                  (105, 120, 140), seed=3)
    items, decisions = merge(dets, judge=lambda a, b: None)
    couch_pair = [p for p in decisions if {p.a, p.b} == {"p1:couch:0", "p2:couch:0"}][0]
    couches = [it for it in items if it.category == "couch"]
    assert couch_pair.outcome == "review", couch_pair
    assert len(couches) == 2, "an undecided pair must stay two items"
    items2, _ = merge(dets, judge=lambda a, b: True)
    assert len([it for it in items2 if it.category == "couch"]) == 1, "judge saying same should merge"
    items3, _ = merge(dets, judge=lambda a, b: False)
    assert len([it for it in items3 if it.category == "couch"]) == 2, "judge saying different should separate"
    print(f"  judge ok: blue-grey couch scored {couch_pair.score:.2f} -> {couch_pair.outcome} when unsure")


def test_schedule(items):
    grey = next(it for it in items if it.category == "couch" and len(it.detections) == 2)
    tv = next(it for it in items if it.category == "television")
    entries = {
        grey.item_id: ClaimantEntry(description="Grey fabric sofa, three seat", room="Living room",
                                    replacement_cost=1200.0, purchased_from="Ashley"),
        tv.item_id: ClaimantEntry(description="65 inch LED TV", brand_model="TCL 65S", room="Living room",
                                  replacement_cost=650.0, purchase_date=date(2020, 1, 15)),
    }
    rows = build_rows(items, entries, as_of=date(2026, 10, 27))
    r_grey = next(r for r in rows if r["item_id"] == grey.item_id)
    r_tv = next(r for r in rows if r["item_id"] == tv.item_id)

    assert r_grey["earliest_photo_date"] == "2019-03-02"
    assert abs(r_grey["age_years"] - 7.7) < 0.1, r_grey["age_years"]
    exact = (date(2026, 10, 27) - date(2019, 3, 2)).days / 365.25            # 7.66 years
    assert r_grey["depreciation_pct"] == round(exact / 10 * 100, 1), r_grey["depreciation_pct"]
    assert r_grey["actual_cash_value"] == f"{1200 * (1 - exact / 10):.2f}", r_grey["actual_cash_value"]
    assert "age from earliest photo" in r_grey["flags"]

    # The photo shows the TV in March 2019; the claimant says bought Jan 2020.
    assert "photo predates stated purchase date" in r_tv["flags"], r_tv["flags"]
    assert r_tv["depreciation_pct"] == 80.0, "capped at the 80% maximum"

    unpriced = [r for r in rows if not r["replacement_cost"]]
    assert unpriced and all("replacement cost needed" in r["flags"] for r in unpriced)
    t = totals(rows)
    assert t["priced_lines"] == 2 and t["replacement_cost_total"] == 1850.0
    out = write_csv(rows, TMP / "schedule.csv")
    print(f"  schedule ok: {t}")
    print(f"  csv: {out}")


def test_no_evidence_no_line():
    from pol.model import Item
    ghost = Item(item_id="item-999", category="couch",
                 detections=[Detection("x", "p.jpg", date(2020, 1, 1), "couch", (0, 0, 10, 10))])
    assert build_rows([ghost], as_of=date(2026, 1, 1)) == [], "an item with no crop must not be claimed"
    print("  evidence rule ok: item without a crop produced no line")


if __name__ == "__main__":
    items = test_merge()
    test_judge_unsure_stays_separate()
    test_schedule(items)
    test_no_evidence_no_line()
    print("all passed")
