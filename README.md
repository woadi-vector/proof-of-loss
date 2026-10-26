# Proof of Loss

After a flood or fire, a homeowner has to list every item they lost, from memory, with an age and a price for each. Whatever they forget goes unpaid. Proof of Loss builds that list from photos they already have: years of camera roll pictures, taken in every home they have lived in, plus a narrated walkthrough of what survived. Every line on the resulting schedule of loss links to the image that proves the item existed.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026) on Meta's SAM 3.1 and Muse models.

## Status

Day one. `scripts/test_sam.py` sends one photo to SAM 3.1 with a list of household-item prompts and draws what it finds.

## What was built during the hackathon

Everything in this repository. The repository was created at the start of the event and contains no code written before it. Third-party dependencies are listed in `requirements.txt`.

## Run it

Python 3.10 or newer.

```
python -m pip install -r requirements.txt
copy .env.example .env      (then paste your key into .env)
python scripts/test_sam.py "path\to\photo.jpg"
```

## License

MIT. SAM 3.1 and the `meta-sam-parser` package are used under Meta's SAM License.
