"""What to ask SAM 3.1 to look for.

Each claim category gets a few phrasings, because one word misses things: on
the first live photo, "side table" found nothing beside a TV stand that has
one. Every phrasing costs one SAM call per photo, so lists stay short.
Detections from different phrasings of the same object collapse in merge.
"""

CATEGORIES: dict[str, list[str]] = {
    "television": ["television"],
    "tv stand": ["tv stand", "media console"],
    "side table": ["side table", "end table", "nightstand"],
    "soundbar": ["soundbar"],
    "speaker": ["speaker"],
    "couch": ["couch", "sofa"],
    "chair": ["armchair", "chair"],
    "table": ["coffee table", "dining table"],
    "lamp": ["lamp"],
    "rug": ["rug"],
    "bookshelf": ["bookshelf"],
    "bed": ["bed"],
    "picture frame": ["picture frame"],
    "ceiling fan": ["ceiling fan"],
}


def all_prompts() -> list[tuple[str, str]]:
    """(category, prompt) pairs in a stable order."""
    return [(cat, p) for cat, phrasings in CATEGORIES.items() for p in phrasings]


def category_for(prompt: str) -> str:
    for cat, phrasings in CATEGORIES.items():
        if prompt in phrasings:
            return cat
    return prompt
