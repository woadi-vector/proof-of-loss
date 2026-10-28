"""First contact with SAM 3.1: segment household items in one old photo.

Usage (from the repo folder):
    python scripts/test_sam.py "C:\\path\\to\\photo.jpg"
    python scripts/test_sam.py photo.jpg --prompts "couch" "television" "lamp"

Writes everything to runs/<timestamp>_<photo>/:
    raw_<prompt>.txt      every server event, verbatim, for debugging
    overlay.jpg           the photo with each found item boxed and labelled
    items.json            one record per item: prompt, object id, box, confidence

Needs MODEL_API_KEY in a .env file at the repo root (never committed).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw
from meta_sam_parser import CompletedOutcome, image_segmentation_format

API = "https://api.meta.ai/v1"
ROOT = Path(__file__).resolve().parent.parent

# Things a contents claim actually lists. Short noun phrases, one object each,
# as the SAM docs recommend.
DEFAULT_PROMPTS = [
    "couch", "television", "lamp", "chair", "table",
    "rug", "picture frame", "bookshelf", "bed", "speaker",
]


def load_key() -> str:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit(f"No .env file at {env}. Copy .env.example to .env and add your key.")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("MODEL_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    sys.exit("MODEL_API_KEY is empty in .env")


def upload(path: Path, key: str) -> str:
    with path.open("rb") as fh:
        r = requests.post(
            f"{API}/files",
            headers={"Authorization": f"Bearer {key}"},
            data={"purpose": "user_data",
                  "expires_after[anchor]": "created_at",
                  "expires_after[seconds]": "3600"},
            files={"file": (path.name, fh)},
            timeout=120,
        )
    if r.status_code >= 400:
        sys.exit(f"Upload failed ({r.status_code}): {r.text[:500]}")
    return r.json()["id"]


def segment(file_id: str, prompt: str, key: str, raw_log: Path) -> str:
    """Stream one SAM request; return the concatenated output text."""
    body = {
        "model": "sam-3.1",
        "stream": True,
        "metadata": {"include_confidence": "true"},
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "file_id": file_id},
            ],
        }],
    }
    r = requests.post(
        f"{API}/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body, stream=True, timeout=300,
    )
    if r.status_code >= 400:
        raw_log.write_text(r.text, encoding="utf-8")
        raise RuntimeError(f"{prompt}: HTTP {r.status_code}: {r.text[:400]}")

    deltas: list[str] = []
    final_text: str | None = None
    with raw_log.open("w", encoding="utf-8") as log:
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            log.write(line + "\n")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            t = ev.get("type", "")
            if t == "response.output_text.delta":
                deltas.append(ev.get("delta", ""))
            elif t == "response.content_part.done":
                part = ev.get("part") or {}
                if part.get("type") == "output_text":
                    final_text = part.get("text", "")
            elif t in ("response.failed", "error"):
                raise RuntimeError(f"{prompt}: server reported {t}: {json.dumps(ev)[:400]}")
    return final_text if final_text is not None else "".join(deltas)


def parse_boxes(text: str) -> list:
    parser = image_segmentation_format().create_parser()
    parser.push(text if text.endswith("\n") else text + "\n")
    result = parser.finish(CompletedOutcome()).result
    return [rec for rec in result.records if rec.kind == "box"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--prompts", nargs="+", default=DEFAULT_PROMPTS)
    args = ap.parse_args()

    photo = Path(args.photo)
    if not photo.exists():
        sys.exit(f"Photo not found: {photo}")
    key = load_key()

    out = ROOT / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_{photo.stem}"
    out.mkdir(parents=True, exist_ok=True)

    print(f"Uploading {photo.name} ...")
    file_id = upload(photo, key)
    print(f"  file id {file_id}")

    items = []
    for prompt in args.prompts:
        t0 = time.time()
        try:
            text = segment(file_id, prompt, key, out / f"raw_{prompt.replace(' ', '_')}.txt")
            boxes = parse_boxes(text)
        except Exception as exc:  # keep going; one bad prompt should not end the run
            print(f"  {prompt:15s} ERROR {exc}")
            continue
        conf = [round(b.confidence, 2) for b in boxes if b.confidence is not None]
        print(f"  {prompt:15s} {len(boxes)} found  conf {conf}  ({time.time() - t0:.1f}s)")
        for b in boxes:
            items.append({"photo": str(photo.resolve()), "prompt": prompt, "object_id": b.object_id,
                          "box": [b.left, b.top, b.right, b.bottom],
                          "confidence": b.confidence})

    (out / "items.json").write_text(json.dumps(items, indent=2), encoding="utf-8")

    img = Image.open(photo).convert("RGB")
    draw = ImageDraw.Draw(img)
    for it in items:
        x1, y1, x2, y2 = it["box"]
        draw.rectangle([x1, y1, x2, y2], outline=(255, 64, 64), width=4)
        label = it["prompt"] + (f" {it['confidence']:.2f}" if it["confidence"] is not None else "")
        draw.text((x1 + 6, y1 + 6), label, fill=(255, 255, 0))
    img.save(out / "overlay.jpg", quality=90)

    print(f"\n{len(items)} items total. Results in {out}")


if __name__ == "__main__":
    main()
