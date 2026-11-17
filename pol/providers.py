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

def _env_file() -> dict[str, str]:
    env = Path(__file__).resolve().parent.parent / ".env"
    out: dict[str, str] = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def google_settings() -> dict[str, str]:
    """Project, location, model and prices, from the environment or .env.

    Sign-in is Application Default Credentials (gcloud auth application-default
    login), so no key lives in .env. Prices are per million tokens and default to
    Gemini 3.8 Flash on the Vertex global endpoint at its introductory rate
    ($0.75 / $3.75 through Dec 31 2026, then $1.50 / $7.50); Google lists no
    separate audio rate for it, so audio defaults to the text rate. Set them in
    .env when the model or the year changes, and
    confirm against the Vertex pricing page. Cost in the ledger is an estimate
    from these numbers and the response's own token counts.
    """
    import os
    f = _env_file()

    def get(k: str, default: str = "") -> str:
        return os.environ.get(k) or f.get(k) or default

    return {"project": get("GOOGLE_CLOUD_PROJECT"),
            "location": get("GOOGLE_CLOUD_LOCATION", "global"),
            "model": get("GOOGLE_MODEL", "gemini-3.8-flash"),
            "price_in": get("GOOGLE_PRICE_IN_PER_M", "0.75"),
            "price_audio_in": get("GOOGLE_PRICE_AUDIO_IN_PER_M", "0.75"),
            "price_out": get("GOOGLE_PRICE_OUT_PER_M", "3.75"),
            "thinking": get("GOOGLE_THINKING", "LOW")}


INLINE_LIMIT = 15 * 1024 * 1024        # Vertex caps an inline request near 20 MB


def _yes_no(v) -> bool | None:
    v = str(v).strip().lower()
    return True if v == "yes" else False if v == "no" else None


TRANSCRIBE_PROMPT = """Transcribe this recording of an insurance adjuster walking a loss site.
Return segments in order, each one sentence or short phrase, with start and end
times in seconds from the start of the recording. Write exactly what was said.
Do not summarize, correct, or add words. Mark unclear speech as [inaudible]."""

NOTES_PROMPT = """You are reading the transcript of an insurance adjuster's narrated walk
through a loss site. Each line is [start seconds] text.

Return:
rooms: each room the adjuster says they are entering, with the time they said it.
  Use only rooms the adjuster names; never infer a room from the items in it.
  damage is what the adjuster says about the room: none, light (smoke, soot,
  light water), burned_through, flooded. If they say nothing, use none.
mentions: each belonging the adjuster names or points out. For each:
  room: the room they were in when they named it ("" if none named yet)
  description: the item in plain words, as specific as the adjuster was
  at_s: the start time of the line where they named it
  category: a short generic category (television, sofa, laptop, ...)
  brand_model: only if the adjuster said it, else ""
  quantity: only if the adjuster said a number, else 1
  condition: destroyed, damaged, undamaged, or unknown, from the adjuster's words only
  stated_age_years: only if the adjuster said an age or a purchase year, else null
  quote: the adjuster's exact words about the item

Use only what the adjuster said. Do not add items that were not named. Do not
judge whether anything is true, inflated or suspicious."""

FRAME_PROMPT = """This is one frame from an insurance adjuster's walk video, taken at the
moment the adjuster named an item.

Item named: {description}
Brand or model said: {brand_model}

Answer:
clear: yes if the frame is sharp and lit well enough to judge what is in it,
  no if it is blurred, dark or blocked, unsure otherwise.
shows_item: yes if the named item is visible in the frame, no if it is not,
  unsure if you cannot tell. Do not guess the brand or model from the picture.
note: one short sentence on what you see that decided the answer."""


class GoogleProvider:
    """Vertex AI Gemini: audio transcription, note extraction, frame checks.

    Structured JSON answers through a response schema; token counts from the
    response's usage metadata go into the Ledger with an estimated cost. A
    frame answer of "unsure" becomes None, which the walk keeps and marks; it
    never drops the item.
    """

    name = "google"

    def __init__(self, project: str | None = None, location: str | None = None,
                 model: str | None = None, client=None):
        cfg = google_settings()
        self.project = project or cfg["project"]
        self.location = location or cfg["location"]
        self.model = model or cfg["model"]
        self.price_in = float(cfg["price_in"])
        self.price_audio_in = float(cfg["price_audio_in"])
        self.price_out = float(cfg["price_out"])
        # Thinking tokens bill as output. These are structured reads, not
        # reasoning tasks, so LOW by default; set GOOGLE_THINKING to MINIMAL,
        # MEDIUM or HIGH in .env, or to DEFAULT to leave the model's own setting.
        self.thinking = cfg["thinking"].strip().upper()
        self.name = f"google:{self.model}"
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not self.project:
                raise RuntimeError(
                    "GOOGLE_CLOUD_PROJECT is not set. Add GOOGLE_CLOUD_PROJECT=<project id> to .env.")
            from google import genai
            self._client = genai.Client(vertexai=True, project=self.project,
                                        location=self.location)
        return self._client

    # one call, measured ------------------------------------------------------

    def _call(self, kind: str, contents: list, schema: dict, ledger: Ledger) -> dict:
        from google.genai import types
        extra = {}
        if self.thinking and self.thinking != "DEFAULT":
            extra["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking)
        cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                          response_json_schema=schema, temperature=0, **extra)
        t = time.perf_counter()
        resp = self.client.models.generate_content(model=self.model, contents=contents,
                                                   config=cfg)
        secs = time.perf_counter() - t
        ledger.add(self._record(kind, resp, secs))
        text = resp.text or ""
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{kind}: model did not return JSON: {text[:200]!r}") from e

    def _record(self, kind: str, resp, secs: float) -> CallRecord:
        u = getattr(resp, "usage_metadata", None)
        if u is None:
            return CallRecord(kind, self.model, secs)
        prompt = u.prompt_token_count or 0
        audio = sum((d.token_count or 0) for d in (u.prompt_tokens_details or [])
                    if str(getattr(d.modality, "value", d.modality)).upper() == "AUDIO")
        out = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        cost = ((prompt - audio) * self.price_in + audio * self.price_audio_in
                + out * self.price_out) / 1_000_000
        return CallRecord(kind, self.model, secs, prompt, out, round(cost, 6))

    # the three jobs ----------------------------------------------------------

    def _audio_part(self, audio: Path):
        from google.genai import types
        audio = Path(audio)
        if audio.stat().st_size <= INLINE_LIMIT:
            return types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/wav")
        # Too long to send inline as WAV: send a compressed copy. The original is untouched.
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ogg = Path(d) / "audio.ogg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-ac", "1",
                            "-c:a", "libopus", "-b:a", "24k", str(ogg)], check=True)
            data = ogg.read_bytes()
        if len(data) > INLINE_LIMIT:
            raise RuntimeError("Walk audio is too long to send in one request (about an hour "
                               "at most). Split the walk into shorter videos.")
        return types.Part.from_bytes(data=data, mime_type="audio/ogg")

    def transcribe(self, audio: Path, ledger: Ledger) -> list[Segment]:
        schema = {"type": "object", "properties": {"segments": {"type": "array", "items": {
            "type": "object",
            "properties": {"start_s": {"type": "number"}, "end_s": {"type": "number"},
                           "text": {"type": "string"}},
            "required": ["start_s", "end_s", "text"]}}}, "required": ["segments"]}
        d = self._call("transcribe", [self._audio_part(audio), TRANSCRIBE_PROMPT], schema, ledger)
        return [Segment(float(s["start_s"]), float(s["end_s"]), str(s["text"]).strip())
                for s in d.get("segments", []) if str(s.get("text", "")).strip()]

    def extract_notes(self, segments: list[Segment], ledger: Ledger) -> WalkNotes:
        if not segments:
            return WalkNotes([], [])
        transcript = "\n".join(f"[{s.start_s:.1f}] {s.text}" for s in segments)
        damages = [d.value for d in RoomDamage]
        conditions = [c.value for c in Condition]
        schema = {"type": "object", "properties": {
            "rooms": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "start_s": {"type": "number"},
                "damage": {"type": "string", "enum": damages}},
                "required": ["name", "start_s", "damage"]}},
            "mentions": {"type": "array", "items": {"type": "object", "properties": {
                "room": {"type": "string"}, "description": {"type": "string"},
                "at_s": {"type": "number"}, "category": {"type": "string"},
                "brand_model": {"type": "string"}, "quantity": {"type": "integer"},
                "condition": {"type": "string", "enum": conditions},
                "stated_age_years": {"type": ["number", "null"]},
                "quote": {"type": "string"}},
                "required": ["room", "description", "at_s", "condition", "quote"]}}},
            "required": ["rooms", "mentions"]}
        d = self._call("notes", [NOTES_PROMPT, transcript], schema, ledger)

        rooms = [RoomEntry(str(r["name"]).strip(), float(r["start_s"]),
                           RoomDamage(r["damage"]) if r.get("damage") in damages else RoomDamage.NONE)
                 for r in d.get("rooms", []) if str(r.get("name", "")).strip()]
        mentions = []
        for i, m in enumerate(d.get("mentions", []), 1):
            age = m.get("stated_age_years")
            mentions.append(Mention(
                mention_id=f"m{i:03d}", room=str(m.get("room", "")).strip(),
                description=str(m["description"]).strip(), at_s=float(m["at_s"]),
                category=str(m.get("category", "")).strip(),
                brand_model=str(m.get("brand_model", "")).strip(),
                quantity=max(1, int(m.get("quantity") or 1)),
                condition=Condition(m["condition"]) if m.get("condition") in conditions
                else Condition.UNKNOWN,
                stated_age_years=float(age) if age is not None else None,
                quote=str(m.get("quote", "")).strip()))
        return WalkNotes(rooms, mentions)

    def check_frame(self, frame: Path, mention: Mention, ledger: Ledger) -> FrameCheck:
        from google.genai import types
        yn = {"type": "string", "enum": ["yes", "no", "unsure"]}
        schema = {"type": "object", "properties": {"clear": yn, "shows_item": yn,
                                                   "note": {"type": "string"}},
                  "required": ["clear", "shows_item", "note"]}
        prompt = FRAME_PROMPT.format(description=mention.description,
                                     brand_model=mention.brand_model or "(none said)")
        img = types.Part.from_bytes(data=Path(frame).read_bytes(), mime_type="image/jpeg")
        d = self._call("frame", [img, prompt], schema, ledger)
        return FrameCheck(_yes_no(d.get("clear")), _yes_no(d.get("shows_item")),
                          str(d.get("note", "")).strip())

    # listings for pricing (pol/comps.py decides what is kept) ------------------

    def find_listings(self, item: str, currency: str, region: str, ledger: Ledger):
        """One grounded search call. Returns what the model proposed plus the
        pages Google's search actually returned, so code can drop anything uncited."""
        from google.genai import types

        from .comps import LISTINGS_PROMPT, SearchAnswer, parse_answer
        extra = {}
        if self.thinking and self.thinking != "DEFAULT":
            extra["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking)
        cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],
                                          temperature=0, **extra)
        prompt = LISTINGS_PROMPT.format(item=item, currency=currency, region=region)
        t = time.perf_counter()
        resp = self.client.models.generate_content(model=self.model, contents=[prompt], config=cfg)
        ledger.add(self._record("comps", resp, time.perf_counter() - t))
        cited, queries = [], []
        for cand in (getattr(resp, "candidates", None) or [])[:1]:
            gm = getattr(cand, "grounding_metadata", None)
            if gm:
                queries = list(gm.web_search_queries or [])
                for ch in gm.grounding_chunks or []:
                    web = getattr(ch, "web", None)
                    if web and web.uri:
                        cited.append(web.uri)
        assumptions, listings = parse_answer(resp.text or "")
        return SearchAnswer(assumptions, listings, cited, queries)
