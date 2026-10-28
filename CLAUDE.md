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
* The model never sets a price. Replacement cost comes from the claimant or is flagged missing.
* Depreciation is a visible, editable straight-line table (`--life`); `MAX_DEPRECIATION` 80%.
* Never merge two detections from the same photo, never across categories; an undecided pair stays two items (a duplicate is caught in review; a wrong merge loses money).
* A detection is rejected only if SAM confidence < 0.65 AND Muse Spark says it is not a standalone item of that category.
* Photo dates come from EXIF only, never file times. Older documented age means MORE depreciation, not less: photos prove ownership; they do not raise payouts. When a photo predates the claimant's stated purchase date, flag it.

## Findings so far

* First live SAM run on `tv.jpg` (Oct 28): television 0.96, soundbar 0.86, lamp 0.69 (partly out of frame), couch 0 (correct negative), tv stand 0.79 correct plus a false 0.58 "tv stand" that was the TV's own pedestal foot; "side table" missed the side table. Led to category synonyms and the Muse Spark verify step.
* Fixed-bin color histograms split the same couch under 7% dimming; replaced with chromaticity plus tolerant luminance.
* Images sent through chat apps lose EXIF dates; use originals from the phone or Google Photos.

## Open items, in order

1. Run `test_sam.py` on `tv.jpg` with all phrasings, then `build_schedule.py --describe`. Confirm the Muse Spark model id works (`muse-spark-1.1` from LiteLLM docs; if 404, find the id in the Meta dashboard model list and set `MUSE_MODEL`). Record cost per photo.
2. Real test set: 40 to 60 original photos across Jason's homes; hand-label items for an honest found/missed/wrong-merge evaluation.
3. Walkthrough video: SAM 3.1 video tracking, plus Muse Voice Transcribe on narration to fill purchase details.
4. UI: photo grid, item cards with proof crops side by side, inventory table with totals, CSV export.
5. Demo video script and recording; README results section; submission text; X post.

## Writing style for anything Jason reads or publishes

No em dashes or en dashes; use commas, colons, semicolons, parentheses or separate sentences. Avoid stock AI vocabulary (delve, leverage, seamless, robust as decoration, crucial, pivotal, unlock, elevate, landscape, navigate, testament, tapestry). No "not just X, it's Y" constructions, no summary closers. Commit as `Jason Wold <281243501+woadi-vector@users.noreply.github.com>`.
