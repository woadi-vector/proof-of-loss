# Synthetic flood contents claim

Invented data for tests and demos. Every brand, model, amount and document name here is made up. It copies only the shape of a real policyholder contents claim, so the tools can be built and shown without client data. Real claims stay in the gitignored `claims/` folder and are never committed.

Regenerate with `python scripts/make_synthetic_claim.py` (fixed seed, same output every time).

## Files

- `lines.csv`: the claimed list, 94 lines. Columns: line, room, item, brand, model, qty, purchase_year, acquired (purchase, gift, unknown), claimed_unit_cad.
- `photo_report.csv`: a contractor photo report. Each photo has a report name, photo number, page, what is visible, branding (branded, probable, none), and the line it supports. Extraction photos support no line.
- `documents.csv`: the documents the policyholder supplied (no actual files). Each has an id, file name, type (card_statement, gift_letter, receipt), what it proves (ownership, value) and the line it supports.
- `expected.json`: the answers tests check against.

## Defects built in on purpose

- The stated total is $437 lower than the lines add up to.
- One line has no quantity and one has no room.
- Eleven luxury lines make up about half the claim. Four are branded in photos, four are probable, three appear in no photo.
- Gifts carry a gift letter (ownership only, no value). Some purchases have a card statement, some have nothing.
