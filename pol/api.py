"""Thin client for Meta Model API: key loading, file upload, Responses calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

API = "https://api.meta.ai/v1"
ROOT = Path(__file__).resolve().parent.parent

# The model id the LiteLLM provider docs list for Muse Spark. If Meta's model
# list names a newer one, set MUSE_MODEL in .env.
DEFAULT_MUSE_MODEL = "muse-spark-1.1"


def _env() -> dict[str, str]:
    env = ROOT / ".env"
    if not env.exists():
        sys.exit(f"No .env file at {env}. Copy .env.example to .env and add your key.")
    out = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_key() -> str:
    key = _env().get("MODEL_API_KEY", "")
    if not key:
        sys.exit("MODEL_API_KEY is empty in .env")
    return key


def muse_model() -> str:
    return _env().get("MUSE_MODEL") or DEFAULT_MUSE_MODEL


def upload(path: str | Path, key: str, ttl_seconds: int = 3600) -> str:
    path = Path(path)
    with path.open("rb") as fh:
        r = requests.post(
            f"{API}/files",
            headers={"Authorization": f"Bearer {key}"},
            data={"purpose": "user_data",
                  "expires_after[anchor]": "created_at",
                  "expires_after[seconds]": str(ttl_seconds)},
            files={"file": (path.name, fh)},
            timeout=120,
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Upload failed ({r.status_code}): {r.text[:500]}")
    return r.json()["id"]


def response_text(body: dict) -> str:
    """Pull the assistant text out of a non-streaming Responses API reply."""
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts = []
    for item in body.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "".join(parts)


def respond(key: str, model: str, content: list[dict], timeout: int = 120) -> str:
    r = requests.post(
        f"{API}/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "input": [{"role": "user", "content": content}]},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
    return response_text(r.json())


def parse_json_reply(text: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the outermost object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object in reply: {text[:200]}")
    return json.loads(text[start:end + 1])
