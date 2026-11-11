"""Turn one recorded walk (glasses or phone video) into first-walk observations.

Steps:
  1. hash the video (sha256) before anything touches it
  2. pull the audio with ffmpeg and transcribe it
  3. read the transcript for rooms entered (with damage the adjuster states)
     and items named
  4. for each item, pull frames around the moment it was named, keep the
     sharpest, and ask whether that frame clearly shows the item
  5. write observations for document mode, the rooms walked, a manifest of
     every file hash, and the cost ledger

Rules:
  * the original video is never modified; frames are new files, each hashed
  * an item whose frame does not clearly show it is still recorded, marked
    not clear, so a person looks at it; it is never silently dropped
  * rooms come only from what the adjuster said; a room never named was not
    walked
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .inventory import Observation
from .providers import FrameCheck, Ledger, Mention, Provider, file_sha256
from .records import Room

FRAME_OFFSETS = (-1.0, -0.5, 0.0, 0.5, 1.0)   # seconds around the moment an item is named


@dataclass
class FramePick:
    mention_id: str
    path: str
    at_s: float
    sha256: str
    sharpness: float
    check: FrameCheck


@dataclass
class WalkResult:
    observations: list[Observation]
    rooms: list[Room]
    frames: list[FramePick]
    manifest: dict
    ledger: Ledger


def _need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is not installed or not on PATH; the walk pipeline needs it.")


def video_duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(video)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def extract_audio(video: Path, dest: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", str(dest)], check=True)
    return dest


def extract_frame(video: Path, at_s: float, dest: Path) -> Path | None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(dest)], check=True)
    return dest if dest.exists() and dest.stat().st_size > 0 else None


def sharpness(path: Path) -> float:
    """Edge energy: higher means sharper. Motion blur and darkness both lower it."""
    with Image.open(path) as im:
        edges = im.convert("L").filter(ImageFilter.FIND_EDGES)
        return float(ImageStat.Stat(edges).var[0])


def _pick_frame(video: Path, m: Mention, duration: float, frames_dir: Path) -> tuple[Path, float, float] | None:
    best = None
    for k, off in enumerate(FRAME_OFFSETS):
        t = min(max(0.0, m.at_s + off), max(0.0, duration - 0.05))
        p = extract_frame(video, t, frames_dir / f"{m.mention_id}_{k}.jpg")
        if p is None:
            continue
        s = sharpness(p)
        if best is None or s > best[2]:
            if best is not None:
                best[0].unlink(missing_ok=True)
            best = (p, t, s)
        else:
            p.unlink(missing_ok=True)
    if best is None:
        return None
    final = frames_dir / f"{m.mention_id}.jpg"
    best[0].replace(final)
    return final, best[1], best[2]


def process_walk(video: Path, provider: Provider, out_dir: Path,
                 ledger: Ledger | None = None) -> WalkResult:
    _need_ffmpeg()
    video, out_dir = Path(video), Path(out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    ledger = ledger or Ledger()

    video_hash = file_sha256(video)
    duration = video_duration(video)
    audio = extract_audio(video, out_dir / "audio.wav")

    segments = provider.transcribe(audio, ledger)
    notes = provider.extract_notes(segments, ledger)

    picks: list[FramePick] = []
    observations: list[Observation] = []
    for m in notes.mentions:
        got = _pick_frame(video, m, duration, frames_dir)
        if got is None:
            check = FrameCheck(None, None, "no frame could be read at this time in the video")
            frame_path, at, sharp, digest = "", m.at_s, 0.0, ""
        else:
            frame_path, at, sharp = got
            check = provider.check_frame(frame_path, m, ledger)
            digest = file_sha256(frame_path)
            picks.append(FramePick(m.mention_id, frame_path.name, round(at, 3), digest,
                                   round(sharp, 2), check))
        observations.append(Observation(
            observation_id=m.mention_id, room=m.room, description=m.description,
            category=m.category, brand_model=m.brand_model, quantity=m.quantity,
            condition=m.condition, stated_age_years=m.stated_age_years,
            frame=str(frame_path) if frame_path else "", timestamp_s=round(at, 3), quote=m.quote,
            clear=bool(check.clear) and bool(check.shows_item)))

    rooms = [Room(r.name, walked=True, damage=r.damage) for r in notes.rooms]
    manifest = {
        "createdUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider.name,
        "video": {"name": video.name, "sha256": video_hash, "durationS": round(duration, 3)},
        "audio": {"name": audio.name, "sha256": file_sha256(audio)},
        "frames": [{"mention": p.mention_id, "name": p.path, "atS": p.at_s, "sha256": p.sha256,
                    "sharpness": p.sharpness, "clear": p.check.clear,
                    "showsItem": p.check.shows_item, "note": p.check.note} for p in picks],
        "rooms": [{"name": r.name, "damage": r.damage.value} for r in rooms],
        "segments": len(segments),
        "mentions": len(notes.mentions),
        "ledger": ledger.summary(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "ledger.json").write_text(json.dumps([asdict(c) for c in ledger.calls], indent=2),
                                         encoding="utf-8")
    return WalkResult(observations, rooms, picks, manifest, ledger)
