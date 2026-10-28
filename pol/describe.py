"""Muse Spark looks at each item's crop: what is it, and is it what SAM said?

The answer is advisory except in one case, decided in code: when SAM was not
confident (below VERIFY_BELOW) AND Muse Spark says the crop is not a
standalone belonging of that category, the detection is rejected. On the
first live photo this is exactly the TV's own pedestal foot that SAM called
a second "tv stand" at 0.58.
"""

from __future__ import annotations

from dataclasses import dataclass

from .api import parse_json_reply, respond, upload

VERIFY_BELOW = 0.65

PROMPT = """You are helping a homeowner document belongings for a property insurance claim.
The image is a crop from one of their photos. An object detector labelled it: "{category}".

Answer with one JSON object and nothing else:
{{
  "is_category": true or false,        // is the main subject really a {category}?
  "standalone_item": true or false,    // a separate belonging someone would list on a claim,
                                       // not a part of something else (a TV's leg, a cable, a shadow)
  "name": "short plain name, e.g. 'two-tier wooden side table'",
  "description": "one sentence a claims adjuster would accept: type, material, color, size class",
  "brand_model": "only if a brand or model is legibly visible in the image, else empty string",
  "condition_visible": "good | worn | damaged | unclear"
}}
Do not guess brands. Do not estimate prices."""


@dataclass
class Verdict:
    is_category: bool
    standalone_item: bool
    name: str
    description: str
    brand_model: str
    condition_visible: str
    raw: str


def describe_crop(crop_path: str, category: str, key: str, model: str) -> Verdict:
    file_id = upload(crop_path, key)
    text = respond(key, model, [
        {"type": "input_text", "text": PROMPT.format(category=category)},
        {"type": "input_image", "file_id": file_id},
    ])
    data = parse_json_reply(text)
    return Verdict(
        is_category=bool(data.get("is_category", False)),
        standalone_item=bool(data.get("standalone_item", False)),
        name=str(data.get("name", "")).strip(),
        description=str(data.get("description", "")).strip(),
        brand_model=str(data.get("brand_model", "")).strip(),
        condition_visible=str(data.get("condition_visible", "")).strip(),
        raw=text,
    )


def should_reject(sam_confidence: float | None, v: Verdict) -> bool:
    """Code decides: only weak detections the model also disowns are dropped."""
    weak = sam_confidence is not None and sam_confidence < VERIFY_BELOW
    return weak and not (v.is_category and v.standalone_item)
