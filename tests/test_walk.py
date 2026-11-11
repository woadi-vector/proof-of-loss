"""Walk pipeline on a synthetic video with a scripted provider. No API calls.

The test video is generated with ffmpeg: a sharp test pattern for the first
4 seconds, then a heavily blurred stretch, with a tone for audio.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date

import pytest

from pol.inventory import build_inventory
from pol.providers import (CachedProvider, FrameCheck, Ledger, Mention, RoomEntry, Segment,
                           StubProvider, WalkNotes, file_sha256)
from pol.records import Condition, RoomDamage
from pol.walk import process_walk, sharpness

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    sharp = d / "sharp.mp4"
    blur = d / "blur.mp4"
    out = d / "walk.mp4"
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-v", "error", *a], check=True)
    run("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(sharp))
    run("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest",
        "-vf", "boxblur=20:5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(blur))
    lst = d / "list.txt"
    lst.write_text(f"file '{sharp}'\nfile '{blur}'\n")
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out))
    return out


def script():
    segments = [Segment(0.0, 2.0, "Living room, smoke damage."),
                Segment(2.0, 4.0, "Samsung sixty-five on the wall, screen cracked, about three years old."),
                Segment(5.0, 7.0, "Grey sectional, soot on the arms.")]
    notes = WalkNotes(
        rooms=[RoomEntry("Living room", 0.0, RoomDamage.LIGHT)],
        mentions=[
            Mention("m1", "Living room", "65 inch TV, screen cracked", 2.5, "television", "Samsung QN65",
                    condition=Condition.DAMAGED, stated_age_years=3,
                    quote="Samsung sixty-five on the wall, screen cracked, about three years old."),
            Mention("m2", "Living room", "grey sectional", 6.0, "couch", condition=Condition.DAMAGED,
                    quote="Grey sectional, soot on the arms."),
        ])
    frames = {"m2": FrameCheck(False, None, "motion blur")}
    return segments, notes, frames


def test_walk_produces_observations_frames_and_manifest(video, tmp_path):
    original = file_sha256(video)
    res = process_walk(video, StubProvider(*script()), tmp_path / "run")

    assert file_sha256(video) == original, "the original video is never modified"
    assert [o.observation_id for o in res.observations] == ["m1", "m2"]
    tv, sofa = res.observations
    assert tv.clear and tv.frame.endswith("m1.jpg")
    assert not sofa.clear, "a blurred frame is kept and marked for a person, not dropped"
    assert sofa.frame, "it still has its frame as evidence"

    m = res.manifest
    assert m["video"]["sha256"] == original
    assert len(m["frames"]) == 2 and all(len(f["sha256"]) == 64 for f in m["frames"])
    assert m["rooms"] == [{"name": "Living room", "damage": "light"}]
    assert (tmp_path / "run" / "manifest.json").exists() and (tmp_path / "run" / "ledger.json").exists()
    assert [r.name for r in res.rooms] == ["Living room"]


def test_sharpest_frame_is_kept(video, tmp_path):
    res = process_walk(video, StubProvider(*script()), tmp_path / "run")
    tv = next(f for f in res.frames if f.mention_id == "m1")
    sofa = next(f for f in res.frames if f.mention_id == "m2")
    assert tv.sharpness > sofa.sharpness * 3, "the sharp stretch scores far above the blurred one"
    leftovers = [p.name for p in (tmp_path / "run" / "frames").iterdir()]
    assert sorted(leftovers) == ["m1.jpg", "m2.jpg"], "only the chosen frame per item is kept"
    assert sharpness(tmp_path / "run" / "frames" / "m1.jpg") == pytest.approx(tv.sharpness, rel=1e-3)


def test_feeds_document_mode(video, tmp_path):
    res = process_walk(video, StubProvider(*script()), tmp_path / "run")
    recs = build_inventory(res.observations, as_of=date(2026, 11, 11))
    assert len(recs) == 2
    assert recs[0].valuation.age_years == 3.0
    assert "frame does not show the item clearly" in recs[1].flags


def test_replay_from_cache_costs_nothing(video, tmp_path):
    cache = tmp_path / "cache"
    first = Ledger()
    process_walk(video, CachedProvider(StubProvider(*script()), cache), tmp_path / "a", first)
    assert first.summary()["liveCalls"] == 4 and first.summary()["cachedCalls"] == 0

    again = Ledger()
    process_walk(video, CachedProvider(StubProvider(*script()), cache), tmp_path / "b", again)
    s = again.summary()
    assert s["liveCalls"] == 0 and s["cachedCalls"] == 4 and s["costUsd"] == 0.0
