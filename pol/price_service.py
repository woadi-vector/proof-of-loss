"""Replacement cost for walk inventory lines, from cited listings.

The cloud half of pricing. For each line: one Google Shopping search (through
SerpApi), the raw answer hashed as the backup, comparables chosen by
comps.select_shopping (new items from trusted stores, everything else set aside
with its reason), and the number computed by pricing.price_from_comps. No model
writes a price or chooses a listing.

When the adjuster said a model number on the walk ("TCL 75S451"), the search
uses it and every kept listing must name that model: like kind and quality from
the adjuster's own words. Without one, the search is the item name and the
result says it is a general price, not a model match.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from typing import Any, Callable

from .comps import find_shopping, select_shopping
from .pricing import price_from_comps

WIDE_SPREAD = 2.5  # highest kept listing over lowest; past this the line is flagged
MAX_LINES = 25  # one search per line; a cap keeps one call from spending a month's quota


class PriceInputError(ValueError):
    pass


def _model_words(brand_model: str) -> list[str]:
    """Words of the spoken brand/model that carry a digit: the model number itself."""
    return [w.lower() for w in re.findall(r"[A-Za-z0-9-]+", brand_model) if any(ch.isdigit() for ch in w)]


def loose_words(brand_model: str) -> list[str]:
    """When no listing title names the model, the brand words and the model's leading digits
    (75 in 75S451: the screen size) are the looser match. Each counted listing is flagged."""
    words = re.findall(r"[A-Za-z0-9-]+", brand_model)
    brand = [w.lower() for w in words if not any(ch.isdigit() for ch in w) and len(w) > 1]
    digits = [m.group(0) for w in words if (m := re.search(r"\d{2,}", w))]
    return brand + digits[:1]


def query_for(description: str, brand_model: str) -> tuple[str, list[str], str]:
    """The search text, the words every kept title must contain, and a note for the reader."""
    model = _model_words(brand_model)
    if brand_model.strip():
        q = f"{brand_model.strip()} {description.strip()}".strip()
        if model:
            return q, model, f"searched for the model the adjuster named: {brand_model.strip()}"
        return q, [], f"searched with the brand the adjuster named ({brand_model.strip()}); no model number"
    return description.strip(), [], "no brand or model named; a general price for this kind of item"


def price_lines(payload: dict, get_json: Callable[[dict], dict], today: date | None = None) -> dict:
    """Price each line. `get_json` performs one SerpApi request (injected, so tests replay answers)."""
    if not isinstance(payload, dict):
        raise PriceInputError("payload must be an object")
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise PriceInputError("lines: a non-empty list is required")
    if len(lines) > MAX_LINES:
        raise PriceInputError(f"lines: at most {MAX_LINES} per call (one search each)")
    currency = str(payload.get("currency") or "USD").upper()
    region = str(payload.get("region") or "US").upper()
    if currency not in {"USD", "CAD"} or region not in {"US", "CA"}:
        raise PriceInputError("currency/region: USD/US or CAD/CA")
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(lines):
        if not isinstance(raw, dict):
            raise PriceInputError(f"lines[{i}]: must be an object")
        line_id = str(raw.get("line_id") or "").strip()
        desc = str(raw.get("description") or "").strip()
        if not line_id or not desc:
            raise PriceInputError(f"lines[{i}]: line_id and description are required")
        bm = str(raw.get("brand_model") or "").strip()
        q, must, note = query_for(desc, bm)
        entry: dict[str, Any] = {"line_id": line_id, "description": desc, "brand_model": bm, "query": q,
                                 "search_note": note}
        answers: list[dict] = []

        def fetch(params: dict) -> dict:
            answers.append(get_json(params))
            return answers[-1]

        try:
            res = find_shopping(line_id, q, fetch, currency, region, must=must, today=today)
        except Exception as e:  # one failed search never sinks the others
            entry.update(error=str(e), replacement_cost=None, basis="", kept=[], set_aside=[],
                         flags=["search failed; no price"], answer_sha256=None)
            out.append(entry)
            continue
        loose_flag = ""
        if must and not res.kept and answers:
            # Stores often leave the model number out of the title. Choose again from the same
            # answer (no second search) on brand and leading digits, and say so on the line.
            loose = loose_words(bm)
            if loose and loose != must:
                again = select_shopping(answers[-1], line_id, q, currency, region, must=loose, today=today,
                                        search_note=note)
                if again.kept:
                    again.answer_sha256 = res.answer_sha256
                    res = again
                    loose_flag = (f"no listing title names the model {bm}; counted listings match "
                                  f"{' and '.join(loose)} only; confirm the model before relying on this price")
        pr = price_from_comps(res.kept, currency, region)
        if loose_flag:
            pr.flags.append(loose_flag)
        prices = [c.price for c in pr.sources]
        if len(prices) >= 2 and min(prices) > 0 and max(prices) / min(prices) > WIDE_SPREAD:
            # Listings this far apart are not the same kind of thing: a number would hide that.
            pr.flags.append(f"wide spread: the highest listing is {max(prices) / min(prices):.1f}x the lowest; "
                            "describe the item (brand, size, material) to price like kind and quality")
        entry.update(
            replacement_cost=pr.replacement_cost,
            currency=pr.currency or currency,
            basis=pr.basis,
            kept=[asdict(c) for c in pr.sources],
            set_aside=[{"source": d.listing.source, "title": d.listing.title, "price": d.listing.price,
                        "url": d.listing.url, "why": d.reason} for d in res.dropped]
                      + [{"source": c.source, "title": c.title, "price": c.price, "url": c.url,
                          "why": "far from the median price, or outside the market"} for c in pr.excluded],
            flags=pr.flags,
            answer_sha256=res.answer_sha256,
            retrieved=today.isoformat(),
        )
        out.append(entry)
    priced = [e for e in out if e.get("replacement_cost") is not None]
    return {"currency": currency, "region": region, "lines": out,
            "priced": len(priced), "unpriced": len(out) - len(priced),
            "total_replacement_cost": round(sum(e["replacement_cost"] for e in priced), 2)}
