"""Find current listings for each claimed line and save them to comps.csv.

  python scripts/find_comps.py claims/<claim>/lines.csv --out claims/<claim>/comps.csv
  python scripts/find_comps.py claims/<claim>/lines.csv --out claims/<claim>/comps.csv --lines 59,87

Sends only each line's item text (plus brand and model if given) to Google
with web search. Keeps a listing only if its page was among the search
results; saves each kept page under <out folder>/comp_pages/ with its SHA-256
and checks the price is on it. Lines already in comps.csv are skipped, so a
stopped run picks up where it left off. Then build the workbook with --comps.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pol.claim_io import load_claim  # noqa: E402
from pol.comps import find_comps  # noqa: E402
from pol.providers import GoogleProvider, Ledger  # noqa: E402

COLS = ["line", "source", "url", "price", "currency", "region", "title", "retrieved", "note"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="comps.csv to write or extend")
    ap.add_argument("--lines", dest="only", help="comma-separated line numbers (default: all)")
    ap.add_argument("--limit", type=int, help="stop after this many lines (for a first look)")
    ap.add_argument("--redo", action="store_true", help="search the --lines again even if already done")
    ap.add_argument("--currency", default="CAD")
    ap.add_argument("--region", default="CA")
    a = ap.parse_args()

    claim = load_claim(a.lines, None, a.currency)
    done = set()
    if a.out.exists():
        with open(a.out, newline="", encoding="utf-8") as f:
            done = {r["line"] for r in csv.DictReader(f)}
    log_path = a.out.with_name("comps_log.jsonl")
    for path in (log_path,):
        if path.exists():
            done |= {json.loads(x)["line"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}
    want = {x.strip() for x in a.only.split(",")} if a.only else None
    if a.redo and want:
        done -= want
        if a.out.exists():  # drop the old listings for redone lines so they are not doubled
            with open(a.out, newline="", encoding="utf-8") as f:
                keep = [r for r in csv.DictReader(f) if r["line"] not in want]
            with open(a.out, "w", newline="", encoding="utf-8") as f:
                w0 = csv.DictWriter(f, fieldnames=COLS)
                w0.writeheader()
                w0.writerows({k: r.get(k, "") for k in COLS} for r in keep)
    todo = [il for il in claim.lines if (want is None or il.line.line_id in want) and il.line.line_id not in done]
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        print("nothing to do: every requested line is already in", a.out)
        return

    provider, ledger = GoogleProvider(), Ledger()
    snaps = a.out.parent / "comp_pages"
    new = not a.out.exists()
    searches = 0
    with open(a.out, "a", newline="", encoding="utf-8") as f, open(log_path, "a", encoding="utf-8") as log:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        for il in todo:
            ln = il.line
            parts = [x for x in (il.brand, il.model) if x and x.lower() not in ln.description.lower()]
            item = " ".join(parts + [ln.description])
            try:
                r = find_comps(ln.line_id, item, provider, ledger, a.currency, a.region, snapshot_dir=snaps)
            except Exception as e:  # one bad line should not stop the run
                print(f"line {ln.line_id} {item!r}: error {e}")
                log.write(json.dumps({"line": ln.line_id, "item": item, "error": str(e)}) + "\n")
                continue
            for c in r.kept:
                w.writerow({"line": ln.line_id, "source": c.source, "url": c.url, "price": c.price,
                            "currency": c.currency, "region": c.region, "title": c.title,
                            "retrieved": c.retrieved, "note": c.note})
            f.flush()
            searches += len(r.queries)
            log.write(json.dumps({"line": ln.line_id, "item": item, "assumptions": r.assumptions,
                                  "queries": r.queries, "cited_pages": r.cited_pages, "kept": len(r.kept),
                                  "dropped": [{"url": d.listing.url, "price": d.listing.price,
                                               "reason": d.reason} for d in r.dropped],
                                  "snapshots": r.snapshots}) + "\n")
            log.flush()
            checked = sum(1 for c in r.kept if not c.note or c.note.startswith("price confirmed"))
            print(f"line {ln.line_id:>3} {item[:40]:<40} kept {len(r.kept)} "
                  f"(price confirmed on page: {checked}), dropped {len(r.dropped)}")

    s = ledger.summary()
    print(f"\n{len(todo)} line(s), {s['liveCalls']} model calls, {searches} web searches, "
          f"about ${s['costUsd']:.4f} in model tokens (searches: first 5,000 a month are free)")
    print(f"listings: {a.out}   page snapshots: {snaps}   details: {log_path}")


if __name__ == "__main__":
    main()
