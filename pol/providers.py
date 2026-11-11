"""Model calls for the walk pipeline, behind one interface.

Three jobs need a model:
  * transcribe the walk's audio into timed segments
  * read the transcript and pull out rooms entered and items named
  * look at one frame and say whether it clearly shows the named item

Every call goes through a Ledger (model, seconds, tokens, cost), so cost per
walk is a measured number. CachedProvider stores each answer by a hash of its
inputs, so demos and tests replay at $0. StubProvider answers from a script
for tests. GoogleProvider is where Vertex AI plugs in; Azure or AWS would be
one more class with the same three methods.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .records import Condition, RoomDamage


@dataclass
class Segment:
    start_s: float
    end_s: float
    text: str


@dataclass
class RoomEntry:
    name: str
    start_s: float
    damage: RoomDamage = RoomDamage.NONE


@dataclass
class Mention:
    mention_id: str
    room: str
    description: str
    at_s: float                         # when the adjuster named or pointed at it
    category: str = ""
    brand_model: str = ""
    quantity: int = 1
    condition: Condition = Condition.UNKNOWN
    stated_age_years: float | None = None
    quote: str = ""


@dataclass
class WalkNotes:
    rooms: list[RoomEntry]
    mentions: list[Mention]


@dataclass
class FrameCheck:
    clear: bool | None                  # the frame is sharp and well lit enough to judge
    shows_item: bool | None             # the named item is visible in it
    note: str = ""


@dataclass
class CallRecord:
    kind: str
    model: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False


@dataclass
class Ledger:
    calls: list[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def summary(self) -> dict:
        live = [c for c in self.calls if not c.cached]
        return {"calls": len(self.calls), "liveCalls": len(live),
                "cachedCalls": len(self.calls) - len(live),
                "costUsd": round(sum(c.cost_usd for c in live), 4),
                "seconds": round(sum(c.seconds for c in live), 2)}


class Provider(Protocol):
    name: str

    def transcribe(self, audio: Path, ledger: Ledger) -> list[Segment]: ...

    def extract_notes(self, segments: list[Segment], ledger: Ledger) -> WalkNotes: ...

    def check_frame(self, frame: Path, mention: Mention, ledger: Ledger) -> FrameCheck: ...


# Serialization ----------------------------------------------------------------

def _notes_to_json(n: WalkNotes) -> dict:
    return {"rooms": [asdict(r) | {"damage": r.damage.value} for r in n.rooms],
            "mentions": [asdict(m) | {"condition": m.condition.value} for m in n.mentions]}


def _notes_from_json(d: dict) -> WalkNotes:
    rooms = [RoomEntry(r["name"], r["start_s"], RoomDamage(r["damage"])) for r in d["rooms"]]
    mentions = [Mention(**(m | {"condition": Condition(m["condition"])})) for m in d["mentions"]]
    return WalkNotes(rooms, mentions)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Cache ------------------------------------------------------------------------

class CachedProvider:
    """Wraps a provider; an answer seen before is replayed from disk at $0."""

    def __init__(self, inner: Provider, cache_dir: Path):
        self.inner = inner
        self.name = f"cached({inner.name})"
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, kind: str, *parts: str) -> Path:
        h = hashlib.sha256("\x1f".join((self.inner.name, kind, *parts)).encode()).hexdigest()
        return self.dir / f"{kind}-{h[:24]}.json"

    def _hit(self, path: Path, kind: str, ledger: Ledger):
        if path.exists():
            ledger.add(CallRecord(kind, self.inner.name, 0.0, cached=True))
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def transcribe(self, audio: Path, ledger: Ledger) -> list[Segment]:
        p = self._key("transcribe", file_sha256(audio))
        hit = self._hit(p, "transcribe", ledger)
        if hit is not None:
            return [Segment(**s) for s in hit]
        out = self.inner.transcribe(audio, ledger)
        p.write_text(json.dumps([asdict(s) for s in out]), encoding="utf-8")
        return out

    def extract_notes(self, segments: list[Segment], ledger: Ledger) -> WalkNotes:
        p = self._key("notes", json.dumps([asdict(s) for s in segments], sort_keys=True))
        hit = self._hit(p, "notes", ledger)
        if hit is not None:
            return _notes_from_json(hit)
        out = self.inner.extract_notes(segments, ledger)
        p.write_text(json.dumps(_notes_to_json(out)), encoding="utf-8")
        return out

    def check_frame(self, frame: Path, mention: Mention, ledger: Ledger) -> FrameCheck:
        p = self._key("frame", file_sha256(frame), mention.description, mention.brand_model)
        hit = self._hit(p, "frame", ledger)
        if hit is not None:
            return FrameCheck(**hit)
        out = self.inner.check_frame(frame, mention, ledger)
        p.write_text(json.dumps(asdict(out)), encoding="utf-8")
        return out


# Stub -------------------------------------------------------------------------

class StubProvider:
    """Answers from a fixed script. For tests and for building without an API key."""

    name = "stub"

    def __init__(self, segments: list[Segment], notes: WalkNotes,
                 frame_answers: dict[str, FrameCheck] | None = None):
        self.segments, self.notes = segments, notes
        self.frame_answers = frame_answers or {}

    def transcribe(self, audio: Path, ledger: Ledger) -> list[Segment]:
        t = time.perf_counter()
        ledger.add(CallRecord("transcribe", self.name, time.perf_counter() - t))
        return list(self.segments)

    def extract_notes(self, segments: list[Segment], ledger: Ledger) -> WalkNotes:
        ledger.add(CallRecord("notes", self.name, 0.0))
        return self.notes

    def check_frame(self, frame: Path, mention: Mention, ledger: Ledger) -> FrameCheck:
        ledger.add(CallRecord("frame", self.name, 0.0))
        return self.frame_answers.get(mention.mention_id, FrameCheck(True, True))


# Google (Vertex AI) -------------------------------------------------------------

class GoogleProvider:
    """Vertex AI Gemini, to be wired once the Google Cloud project is attached.

    Planned: transcribe and extract_notes as audio and text calls with a JSON
    response schema; check_frame as one image plus the item description, asking
    for clear / shows_item / note, with "unsure" allowed. Token counts and cost
    come from the response's usage metadata into the Ledger.
    """

    name = "google"

    def __init__(self, project: str | None = None, location: str = "us-central1",
                 model: str | None = None):
        self.project, self.location, self.model = project, location, model

    def _not_yet(self):
        raise NotImplementedError(
            "Google is not attached yet. Run with --provider stub, or replay a cached walk.")

    def transcribe(self, audio: Path, ledger: Ledger) -> list[Segment]:
        self._not_yet()

    def extract_notes(self, segments: list[Segment], ledger: Ledger) -> WalkNotes:
        self._not_yet()

    def check_frame(self, frame: Path, mention: Mention, ledger: Ledger) -> FrameCheck:
        self._not_yet()
