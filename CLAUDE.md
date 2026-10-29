# Proof of Loss: working notes for Claude Code

Read this first. It carries the decisions made before this repo had a Claude Code session.

## What this is

Entry for the **Meta Global AI Developer Hackathon** (Cerebral Valley), startup track, theme "Build for Your World". Builder: Jason Wold, who worked post-hurricane property claims as a field adjuster in Florida.

After a flood or fire, a homeowner must list every belonging they lost, with age and price. What they forget goes unpaid. Proof of Loss builds that **contents** schedule of loss from photos they already have (camera roll across every home they have lived in, plus a narrated walkthrough video), and every line links to the image proving the item existed.

Structure and rebuild estimating (Xactimate) is roadmap only: Verisk's line codes and price lists are proprietary, and using them would break the rights rule. Who pays: public adjusters, as client intake.

## Deadlines and rules (times Central)

* Submissions close **Wed Nov 4, 8:00 PM Central** (9 PM ET). Aim to submit by noon that day.
* **New work only.** Everything in this repo was written during the event (it opened Oct 26). Keep it that way: no pasting in code from Jason's other projects.
* Repo must stay **public**. Never commit `.env`, API keys, or Jason's personal photos (`photos/`, `runs/`, `out/` are gitignored; keep it so).
* **3-minute demo video** is required and is a main judging artifact.
* Banned: AI medical advice, simple image analyzers, basic RAG, sports analyzers, and anything using code, data or assets we lack rights to. The "not a simple image analyzer" case rests on: video plus camera roll, cross-photo merging, evidence-linked claim output.
* Judging: Impact 30%, Use of Meta AI tooling 25%, Technical depth 20%, Demo 25%.
* Bonus prizes worth aiming at: Most Viral on X (post demo after submitting), Best Agentic, Best Build on Meta Open Weights (Muse Spark Open via Modal or Baseten; credits requested).
* Meta office hours: Discord #office-hours, weekdays 9 to 10 AM Central.

## Machine

Runs on Jason's **Windows PC**, PowerShell. Repo at `C:\Users\jmanw\Desktop\proof-of-loss`. Test photos in `C:\Users\jmanw\Desktop\pol-photos`. Python 3.12. `.env` holds `MODEL_API_KEY` (and optional `MUSE_MODEL`). Billing is active with a spend limit; Meta gave $150 in credits. Be frugal with calls: report cost-relevant counts.

Jason drives from phone or laptop through Remote Control. Run commands yourself; do not hand him PowerShell to type.

## Pipeline

1. `scripts/test_sam.py <photo>`: SAM 3.1 (`sam-3.1`, Responses API, streamed) with every phrasing in `pol/prompts.py`; writes `runs/<stamp>_<photo>/items.json` and `overlay.jpg`.
2. `scripts/build_schedule.py [--describe]`: crops each detection, reads EXIF date, dedupes within photo, optionally asks Muse Spark to name and verify each crop, merges across photos, writes `out/schedule.csv` and `out/items.json`.
3. `tests/test_pipeline.py`: offline tests (merge, schedule, reject rule). Run after any change to `pol/`.

Modules: `pol/api.py` (Meta client), `pol/prompts.py`, `pol/photos.py`, `pol/merge.py`, `pol/describe.py`, `pol/schedule.py`, `pol/model.py`.

## Rules enforced in code (do not weaken)

* No evidence, no line: every schedule row cites a photo and crop.
* No price without sources. Replacement cost is the claimant's receipt or stated price if given; otherwise the average of cited comparable listings, computed by code in `pol/pricing.py` (each comp needs source, URL and price; with 3+ comps, listings under 40% or over 250% of the median are set aside as not like kind and quality; fewer than 2 comps is flagged as thin support). A model may FIND listings; it never writes the number. Jason's reason: negotiating price is the most aggravating part of a contents claim, so every price must arrive with its backup.
* Every row explains its own depreciation in plain words (`depreciation_explained`) and shows `recoverable_depreciation`, the amount a replacement cost policy pays back once the item is replaced; claimants often never collect it.
* Depreciation is a visible, editable straight-line table (`--life`); `MAX_DEPRECIATION` 80%.
* Never merge two detections from the same photo, never across categories; an undecided pair stays two items (a duplicate is caught in review; a wrong merge loses money).
* A detection is rejected only if SAM confidence < 0.65 AND Muse Spark says it is not a standalone item of that category.
* Photo dates come from EXIF only, never file times. Older documented age means MORE depreciation, not less: photos prove ownership; they do not raise payouts. When a photo predates the claimant's stated purchase date, flag it.

## Findings so far

* First live SAM run on `tv.jpg` (Oct 28): television 0.96, soundbar 0.86, lamp 0.69 (partly out of frame), couch 0 (correct negative), tv stand 0.79 correct plus a false 0.58 "tv stand" that was the TV's own pedestal foot; "side table" missed the side table. Led to category synonyms and the Muse Spark verify step.
* Full pipeline on `tv.jpg` (Oct 28, all 20 phrasings, then `--describe`): `muse-spark-1.1` works as the model id, so no `MUSE_MODEL` needed. Muse Spark named all 4 kept items (TV, soundbar, barn-door TV stand, floor lamp) and the reject rule dropped the 0.58 pedestal foot ("black plastic TV leg", not standalone). "speaker" 0.53 hit the soundbar's exact box and collapsed into it. Side table still missed by "side table", "end table" and "nightstand". The lamp crop is 36 px wide, yet Muse described it in detail; treat descriptions of tiny crops as weak.
* Cost per photo, about $0.08: SAM 3.1 is 20 calls, one image each (about $0.05 at a reported $2.50 per 1,000 images); Muse Spark is 5 calls, 3,240 input tokens (2,927 cached) and 6,273 output (about $0.03 at $1.25 / $0.15 cached / $4.25 per M). 94% of Muse output is reasoning tokens. Prices are from press reports, not Meta's pricing page; confirm in the dashboard. `pol/api.py respond()` discards the `usage` block, so cost is not logged by the pipeline itself.
* Fixed-bin color histograms split the same couch under 7% dimming; replaced with chromaticity plus tolerant luminance.
* Images sent through chat apps lose EXIF dates; use originals from the phone or Google Photos.

## Open items, in order

0. Comparable finder (`pol/comps.py`, new): for each item, use Muse Spark with search grounding (docs: https://dev.meta.ai/docs/search-grounding) to find 3 to 5 current listings of like kind and quality, returned as structured JSON (source, url, title, price, retrieved date). Feed them to `ClaimantEntry.comparables`; `pol/pricing.py` does the math. Prompt must forbid inventing listings; drop any comp whose URL the response did not actually cite. Add `--price` to `build_schedule.py`. Log cost.
1. Real test set: 40 to 60 original photos across Jason's homes; hand-label items for an honest found/missed/wrong-merge evaluation.
2. Walkthrough video: SAM 3.1 video tracking, plus Muse Voice Transcribe on narration to fill purchase details.
3. UI: photo grid, item cards with proof crops side by side, inventory table with totals, CSV export.
4. Demo video script and recording; README results section; submission text; X post.

## Writing style for anything Jason reads or publishes

No em dashes or en dashes; use commas, colons, semicolons, parentheses or separate sentences. Avoid stock AI vocabulary (delve, leverage, seamless, robust as decoration, crucial, pivotal, unlock, elevate, landscape, navigate, testament, tapestry). No "not just X, it's Y" constructions, no summary closers. Commit as `Jason Wold <281243501+woadi-vector@users.noreply.github.com>`.
