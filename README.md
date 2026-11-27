# Proof of Loss

**A field adjuster walks a flooded or burned house with no signal. Gemini Nano on the phone reads the walk and names what is in each room; building parts, pets and anything shown on a TV screen are set aside on screen with the reason. When signal returns, the cloud hears the adjuster's narration and prices each line from listings anyone can open. Every line stays Needs review until a person signs it off.**

Entry for **#BuildHybridAI 2026** (Firebase AI Logic). Built by Jason Wold, who worked post-hurricane property and contents claims as a field adjuster in Florida.

- Review page: **https://proof-of-loss.web.app**
- Android app: `android/` (Kotlin, Jetpack Compose, CameraX)
- Cloud Functions: `functions/` (Python), shared claim code in `pol/`

## Why hybrid

The houses with the worst losses have the worst signal. A contents inventory cannot wait for a cell tower, so seeing happens on the phone. The jobs the phone cannot do (hearing speech, searching retail listings) wait for signal and run in the cloud. Each answer records where it ran.

| Step | Where it runs | What it does |
|---|---|---|
| Name the items in each frame | **On device**: Gemini Nano through Firebase AI Logic hybrid inference (`PREFER_ON_DEVICE`) | Up to six household items per frame, with any brand the model can read. Works in airplane mode. |
| A frame Gemini Nano declines | **Cloud**: Gemini through Firebase AI Logic | The decline and its reason stay on the card; the adjuster can choose a cloud read. |
| Hear the narration | **Cloud**: Gemini through Firebase AI Logic | A word-for-word transcript with times, and the rooms, items, brands, models and years the adjuster said. |
| Price each inventory line | **Cloud**: `price_lines` Cloud Function | One Google Shopping search per line; code chooses the listings and does the math. |
| Check a sworn schedule against the walk | **Cloud**: `check_claim` Cloud Function | Six statuses, every result Needs review. |

App Check guards every cloud call: the debug provider on the phone, reCAPTCHA Enterprise on the review page.

## What a judge can see on screen

- **Set aside, with the reason.** Walls, doors, outlets and wiring go to "Set aside as part of the building"; dogs and people to "Set aside, not property". Nothing is silently dropped.
- **Held for the adjuster.** Clothing seen only in frames that also show a TV is flagged "may be on the TV screen, not in the room; adjuster to confirm". A brand read once is marked "unconfirmed". An item named twice at different moments is flagged "one item or two".
- **Voice and camera on one line.** The camera's blurry brand reads ("CPRO-FORM x2, C-FORM") sit next to what the adjuster said ("said: bike (ProForm)"). Code never settles which is right.
- **Hashes, re-checked.** The phone hashes every photo, frame and video (SHA-256) the moment it exists. The review page hashes each file again and shows "N of M files match"; a changed or missing file is listed and never used as evidence. A video from Meta glasses is labelled "hashed when this phone received it, not when the glasses recorded it".
- **Prices with their backup.** Each price is the average of new-retail listings from established stores, each linked. Refurbished, resale, marketplace, accessory and different-model listings are set aside with the reason. When the adjuster said a model number, every counted listing must name that model. A wide spread of prices is flagged instead of averaged away.
- **A claim workbook.** Summary, Inventory (replacement cost only; actual cash value is the carrier's to apply), Sources (every listing, counted or set aside) and Evidence (every file and its hash check).

## Models observe, code decides

The models only name items, transcribe speech and answer yes, no or unsure. Plain code owns everything that has consequences: the structure and pet set-asides, the roll-up of repeat sightings, the statuses, which listings count, and every number. No model writes a price. Nothing states fraud, a score or a probability.

## The walk, step by step

1. Enter the claim number and loss address. Capture is locked until the claim number is in.
2. Record a walk in the app, or add one recorded on Meta glasses (today's videos are listed, glasses folders first). Never trim or edit a video first: it is evidence.
3. The app pulls frames where the scene changes, keeps the sharpest of three, and Gemini Nano reads each one. Gemini Nano runs only while the app is on screen; a read that stops when the adjuster switches away resumes on return.
4. With signal, the narration is heard and each named item gets a frame at the moment it was said.
5. A walk cannot be closed until its video is added and every frame is read. Closing without the narration (no signal) is an explicit choice, recorded on the video.
6. Share the walk (`walk.json` plus every file). On the review page, open it, check the hashes, price the lines, and download the workbook.

## Run it

**Review page**: open https://proof-of-loss.web.app, click **Open a walk from the phone**, and pick `walk.json` with every photo and video the phone shared. Or use **Load sample claim** for the synthetic claim in `tests/fixtures/synthetic_flood_claim/` (invented data).

**Android app** (needs a phone with Gemini Nano through AICore, such as a Galaxy S25 or Z Fold7, or a Pixel 9 or later):
1. Add your own `android/app/google-services.json` from a Firebase project with AI Logic enabled (the file is not in the repo).
2. Open `android/` in Android Studio and Run.
3. Register the App Check debug token that Logcat prints (`DebugAppCheckProvider`) under App Check, Apps, your Android app, Manage debug tokens.

**Functions and hosting**:
```
python -m venv functions/venv
functions/venv/Scripts/python -m pip install -r functions/requirements.txt   (Windows path)
firebase functions:secrets:set SERPAPI_KEY
firebase deploy --only functions,hosting
```

**Tests**: `python -m pytest -q tests` (Python) and `npm --prefix web test` (web). Pricing tests replay recorded search answers, so they use no searches.

## Built during #BuildHybridAI, and what was reused

Everything on `main` before Nov 11, 2026, 1:30 pm PT counts as reused. Exact first-commit dates come from `git log`.

**Reused (on main before the cutoff):**
- Oct 26 to 28, for the Meta Global AI Developer Hackathon: the SAM 3.1 and Muse Spark photo pipeline (`pol/api.py`, `prompts.py`, `photos.py`, `merge.py`, `describe.py`, `schedule.py`, `model.py`, `evaluate.py`, `pricing.py`, `scripts/test_sam.py`, `build_schedule.py`, `evaluate.py`). The old README is in `docs/meta-hackathon-2026.md`. None of it runs in this entry except `pol/pricing.py`, the averaging and outlier rule used by `price_lines`.
- Nov 11, 8:19 to 8:43 am PT: carrier-side claim modules (`pol/records.py`, `inventory.py`, `matcher.py`, `valuation.py`, `lkq.py`, `providers.py`, `walk.py`, `scripts/walk.py`) and their tests. The same contents code also lives in the Clarice project's `contents-service/` folder, shared by agreement with its author. `check_claim` runs on `records.py`, `matcher.py`, `valuation.py` and `lkq.py`.

**Built in the window (first committed after the cutoff):**
- The Android app: capture, frame pulling, Gemini Nano hybrid reads, narration, Meta glasses import, room roll-up, set-asides, walk gate (`android/`, Nov 20 to 27).
- The review page: walk import and hash re-check, inventory, pricing, workbook (`web/`, Nov 20 to 27).
- Cloud Functions `check_claim` and `price_lines`, and `pol/service.py`, `pol/price_service.py` (Nov 11 afternoon to Nov 27).
- Claim import, evidence register, support status, workbook exporter and comps finder (`pol/claim_io.py`, `evidence.py`, `support.py`, `export.py`, `comps.py`, Nov 14 to 17), first built for a contents job and reused here for pricing. Reused files above were also changed during the window; `git log` shows each change.

## Data and privacy

The repo holds no real claim. The sample claim is invented. Real client files go only in the git-ignored `claims/` folder, only with written consent, and are never committed. Keys live in `.env` (git-ignored) or Secret Manager.

## License

MIT. See `LICENSE`.
