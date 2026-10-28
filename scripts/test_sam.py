"""First contact with SAM 3.1: segment household items in one old photo.

Usage (from the repo folder):
    python scripts/test_sam.py "C:\\path\\to\\photo.jpg"          (every category in pol/prompts.py)
    python scripts/test_sam.py photo.jpg --prompts "end table" "lamp"

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
from PIL import Image, ImageDraw, ImageFont
from meta_sam_parser import CompletedOutcome, image_segmentation_format

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pol.api import load_key, upload  # noqa: E402
from pol.prompts import all_prompts, category_for  # noqa: E402

API = "https://api.meta.ai/v1"
ROOT = Path(__file__).resolve().parent.parent



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
    ap.add_argument("--prompts", nargs="+", help="override the category phrasings in pol/prompts.py")
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

    pairs = [(category_for(p), p) for p in args.prompts] if args.prompts else all_prompts()
    items = []
    for category, prompt in pairs:
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
            items.append({"photo": str(photo.resolve()), "category": category, "prompt": prompt,
                          "object_id": b.object_id,
                          "box": [b.left, b.top, b.right, b.bottom],
                          "confidence": b.confidence})

    (out / "items.json").write_text(json.dumps(items, indent=2), encoding="utf-8")

    img = Image.open(photo).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=max(18, img.width // 45))
    except TypeError:          # Pillow older than 10.1
        font = ImageFont.load_default()
    for it in items:
        x1, y1, x2, y2 = it["box"]
        draw.rectangle([x1, y1, x2, y2], outline=(255, 64, 64), width=4)
        label = it["prompt"] + (f" {it['confidence']:.2f}" if it["confidence"] is not None else "")
        draw.text((x1 + 6, y1 + 6), label, fill=(255, 255, 0), font=font,
                  stroke_width=2, stroke_fill=(0, 0, 0))
    img.save(out / "overlay.jpg", quality=90)

    print(f"\n{len(items)} items total. Results in {out}")


if __name__ == "__main__":
    main()
