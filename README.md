# Proof of Loss

After a flood or fire, a homeowner has to list every item they lost, from memory, with an age and a price for each. Whatever they forget goes unpaid. Proof of Loss builds that list from photos they already have: years of camera roll pictures, taken in every home they have lived in, plus a narrated walkthrough of what survived. Every line on the resulting schedule of loss links to the image that proves the item existed.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026) on Meta's SAM 3.1 and Muse models.

## How it works

1. **Find** (`scripts/test_sam.py`): each photo goes to SAM 3.1 with short prompts for things a contents claim lists (couch, television, lamp). Every match comes back as a box and a pixel mask.
2. **Crop and date** (`pol/photos.py`): each object is cut out of its photo as evidence, and the photo's EXIF date is read. File dates are never used, because copying a photo resets them.
3. **Merge** (`pol/merge.py`): the same couch photographed in 2019 and again in 2023, in a different home, becomes one item dated from the earliest photo. Code decides the clear cases; plausible pairs go to Muse Spark; an undecided pair stays two items. Two objects in one photo are never merged.
4. **Schedule** (`pol/schedule.py`): one line per item in the columns carriers ask for, with replacement cost, depreciation and actual cash value.

Rules enforced in code, whatever a model says:

* No evidence, no line. Every row cites the photo and crop that prove the item existed.
* The model never sets a price. Replacement cost comes from the claimant or is flagged as missing.
* Depreciation uses a visible, editable table (`--life`), not a hidden estimate.
* When a photo predates the purchase date the claimant gave, the line is flagged, so the claimant can correct it before an adjuster finds it.

## Status

Day two. SAM 3.1 test script, merge, and schedule of loss are written; merge and schedule are covered by `tests/test_pipeline.py` on synthetic data. Live SAM runs pending API billing.

## What was built during the hackathon

Everything in this repository. The repository was created at the start of the event and contains no code written before it. Third-party dependencies are listed in `requirements.txt`.

## Run it

Python 3.10 or newer.

```
python -m pip install -r requirements.txt
copy .env.example .env      (then paste your key into .env)
python scripts/test_sam.py "path\to\photo.jpg"
python scripts/build_schedule.py
python tests/test_pipeline.py
```

## License

MIT. SAM 3.1 and the `meta-sam-parser` package are used under Meta's SAM License.
