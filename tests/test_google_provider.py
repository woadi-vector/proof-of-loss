"""GoogleProvider with a fake client: request shape, parsing, ledger cost. No network."""

import json
import struct
import wave
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("google.genai")

from pol.providers import GoogleProvider, Ledger, Mention, Segment  # noqa: E402
from pol.records import Condition, RoomDamage  # noqa: E402


class FakeModels:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        body, usage = self.answers.pop(0)
        return NS(text=json.dumps(body), usage_metadata=usage)


def usage(prompt, out, audio=0, thoughts=0):
    details = [NS(modality=NS(value="AUDIO"), token_count=audio)] if audio else []
    return NS(prompt_token_count=prompt, candidates_token_count=out,
              thoughts_token_count=thoughts, prompt_tokens_details=details)


def provider(answers):
    models = FakeModels(answers)
    p = GoogleProvider(project="test", location="us-central1", model="gemini-2.5-flash",
                       client=NS(models=models))
    p.price_in, p.price_audio_in, p.price_out = 0.30, 1.00, 2.50
    return p, models


def tiny_wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * 1600)
    return path


def test_transcribe_parses_segments_and_logs_audio_cost(tmp_path):
    p, models = provider([({"segments": [{"start_s": 0, "end_s": 2.5, "text": "Entering the living room."},
                                         {"start_s": 3, "end_s": 4, "text": "  "}]},
                           usage(prompt=1100, out=100, audio=1000))])
    led = Ledger()
    segs = p.transcribe(tiny_wav(tmp_path / "a.wav"), led)
    assert segs == [Segment(0.0, 2.5, "Entering the living room.")]
    rec = led.calls[0]
    assert rec.kind == "transcribe" and rec.input_tokens == 1100 and rec.output_tokens == 100
    assert rec.cost_usd == pytest.approx((100 * 0.30 + 1000 * 1.00 + 100 * 2.50) / 1e6)
    assert models.calls[0][2].response_mime_type == "application/json"


def test_extract_notes_assigns_ids_and_keeps_only_valid_enums():
    body = {"rooms": [{"name": "Living room", "start_s": 0, "damage": "flooded"},
                      {"name": "Den", "start_s": 30, "damage": "smoky"}],
            "mentions": [{"room": "Living room", "description": "65 inch TV", "at_s": 4,
                          "category": "television", "brand_model": "Samsung QN65", "quantity": 0,
                          "condition": "destroyed", "stated_age_years": 3, "quote": "65 inch Samsung"},
                         {"room": "Den", "description": "recliner", "at_s": 33,
                          "condition": "wrecked", "quote": "recliner"}]}
    p, _ = provider([(body, usage(500, 300, thoughts=200))])
    led = Ledger()
    notes = p.extract_notes([Segment(0, 2, "Living room.")], led)
    assert [r.damage for r in notes.rooms] == [RoomDamage.FLOODED, RoomDamage.NONE]
    tv, chair = notes.mentions
    assert (tv.mention_id, tv.quantity, tv.condition, tv.stated_age_years) == \
        ("m001", 1, Condition.DESTROYED, 3.0)
    assert chair.mention_id == "m002" and chair.condition == Condition.UNKNOWN
    assert chair.stated_age_years is None
    assert led.calls[0].output_tokens == 500          # thinking tokens are billed as output


def test_extract_notes_skips_the_call_with_no_speech():
    p, models = provider([])
    notes = p.extract_notes([], Ledger())
    assert notes.rooms == [] and notes.mentions == [] and models.calls == []


def test_check_frame_maps_unsure_to_none(tmp_path):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    p, models = provider([({"clear": "yes", "shows_item": "unsure", "note": "TV half out of frame"},
                           usage(300, 20))])
    m = Mention("m001", "Living room", "65 inch TV", 4.0)
    fc = p.check_frame(frame, m, Ledger())
    assert (fc.clear, fc.shows_item, fc.note) == (True, None, "TV half out of frame")
    assert "65 inch TV" in models.calls[0][1][1]


def test_bad_json_is_an_error_not_a_guess(tmp_path):
    p, models = provider([])
    models.generate_content = lambda **k: NS(text="not json", usage_metadata=None)
    with pytest.raises(RuntimeError, match="did not return JSON"):
        p.extract_notes([Segment(0, 1, "hi")], Ledger())


def test_missing_project_says_what_to_set(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    p = GoogleProvider(project="")
    p.project = ""
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        _ = p.client
