"""Generate the synthetic flood contents claim used by tests and demos.

Everything here is invented: brands, models, amounts and documents. It copies
only the shape of a real policyholder claim (about 94 lines, a few luxury
lines making up about half the total, a stated total that does not match the
lines, a contractor photo report, and a mix of purchases and gifts), so the
tools can be built and shown without any client data.

  python scripts/make_synthetic_claim.py

writes tests/fixtures/synthetic_flood_claim/{lines.csv, photo_report.csv,
documents.csv, expected.json}. The output is deterministic (fixed seed).
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "synthetic_flood_claim"

ROOMS = ["Basement bedroom", "Basement family room", "Basement office", "Laundry room", "Storage room"]

# The luxury lines: invented fashion houses, about half the claim by value.
LUXURY = [
    ("Maison Verel", "Quilted shoulder bag", "VR-QS22", 4380, "purchase"),
    ("Maison Verel", "Mini chain bag", "VR-MC19", 3640, "gift"),
    ("Atelier Corvane", "Leather tote, large", "AC-T88", 6210, "purchase"),
    ("Atelier Corvane", "Top handle bag", "AC-TH41", 5970, "gift"),
    ("Halsted & Pryor", "Calfskin sneakers", "HP-S07", 1860, "purchase"),
    ("Halsted & Pryor", "Suede loafers", "HP-L12", 2240, "unknown"),
    ("Ostrava Paris", "Knee boots, black", "OP-KB3", 3510, "purchase"),
    ("Ostrava Paris", "Ankle boots, heeled", "OP-AB9", 3090, "gift"),
    ("Ostrava Paris", "Chain crossbody", "OP-CX5", 2470, "purchase"),
    ("Maison Verel", "Cosmetics pouch", "VR-CP02", 690, "gift"),
    ("Linden Rowe", "Cashmere coat", "LR-CC61", 2860, "purchase"),
]

# Everyday lines: (room, item, brand, model, unit price range CAD, max qty)
EVERYDAY = [
    ("Basement family room", "Sectional sofa", "Brookline Home", "BH-SEC4", (1400, 2200), 1),
    ("Basement family room", "Area rug 8x10", "Tessly", "TS-810", (300, 650), 1),
    ("Basement family room", "55 inch television", "Varion", "VX55Q", (550, 900), 1),
    ("Basement family room", "Soundbar", "Varion", "VS-300", (180, 320), 1),
    ("Basement family room", "Game console", "Kestrel", "K5", (480, 650), 1),
    ("Basement family room", "Game controllers", "Kestrel", "KC-2", (70, 95), 3),
    ("Basement family room", "Ottoman", "Brookline Home", "", (120, 260), 1),
    ("Basement family room", "Floor lamp", "Lumio", "LM-F2", (80, 180), 2),
    ("Basement family room", "Throw blankets", "", "", (40, 90), 4),
    ("Basement family room", "Board games", "", "", (25, 60), 6),
    ("Basement bedroom", "Queen mattress", "Restwell", "RW-Q12", (700, 1300), 1),
    ("Basement bedroom", "Bed frame", "Brookline Home", "BH-QF", (400, 800), 1),
    ("Basement bedroom", "Dresser", "Norbeck", "NB-D6", (350, 700), 1),
    ("Basement bedroom", "Nightstands", "Norbeck", "NB-N1", (120, 220), 2),
    ("Basement bedroom", "Bedding set", "", "", (90, 200), 2),
    ("Basement bedroom", "Winter jackets", "Fjellhaus", "", (180, 420), 3),
    ("Basement bedroom", "Dress shoes", "Carrow", "", (110, 240), 3),
    ("Basement bedroom", "Winter boots", "Fjellhaus", "FH-WB", (150, 260), 2),
    ("Basement bedroom", "Jeans", "", "", (60, 140), 6),
    ("Basement bedroom", "Sweaters", "", "", (50, 120), 5),
    ("Basement bedroom", "Children's toys, assorted", "", "", (20, 80), 8),
    ("Basement office", "Desktop computer", "Quorra", "QD-7", (1200, 2100), 1),
    ("Basement office", "27 inch monitor", "Quorra", "QM-27", (280, 450), 2),
    ("Basement office", "Office chair", "Seatwise", "SW-ERG", (300, 600), 1),
    ("Basement office", "Desk", "Norbeck", "NB-DK2", (250, 500), 1),
    ("Basement office", "Printer", "Inkwell", "IW-480", (180, 340), 1),
    ("Basement office", "Shelving unit", "Norbeck", "", (90, 180), 2),
    ("Basement office", "Books", "", "", (15, 40), 12),
    ("Laundry room", "Washer", "Kelvane", "KW-500", (900, 1400), 1),
    ("Laundry room", "Dryer", "Kelvane", "KD-500", (850, 1300), 1),
    ("Laundry room", "Cordless vacuum", "Airlyn", "AL-V9", (550, 900), 1),
    ("Laundry room", "Dehumidifier", "Airlyn", "AL-D35", (250, 420), 1),
    ("Laundry room", "Air purifier", "Airlyn", "AL-P2", (200, 380), 1),
    ("Laundry room", "Laundry baskets", "", "", (15, 35), 3),
    ("Laundry room", "Ironing board and iron", "Pressa", "", (60, 140), 1),
    ("Storage room", "Plastic storage bins", "", "", (12, 30), 10),
    ("Storage room", "Wicker baskets", "", "", (25, 60), 4),
    ("Storage room", "Holiday decorations", "", "", (80, 300), 1),
    ("Storage room", "Camping tent", "Ridgeline", "RL-T4", (220, 420), 1),
    ("Storage room", "Sleeping bags", "Ridgeline", "RL-SB", (90, 180), 2),
    ("Storage room", "Luggage set", "Tavelle", "TV-3PC", (300, 650), 1),
    ("Storage room", "Bicycle", "Pacer", "PC-21", (600, 1100), 1),
    ("Storage room", "Tool set", "Ironclad", "IC-200", (150, 300), 1),
    ("Storage room", "Space heater", "Lumio", "LM-H1", (60, 120), 1),
    ("Storage room", "Spare dishes", "", "", (60, 150), 1),
]

MISSING_TOTAL_OFFSET = -437  # stated total is $437 lower than the lines add up to


def build():
    rnd = random.Random(20261114)
    lines = []

    for i, (brand, item, model, price, how) in enumerate(LUXURY):
        lines.append({"room": ROOMS[i % 2], "item": item, "brand": brand, "model": model,
                      "qty": 1, "purchase_year": rnd.choice([2021, 2022, 2023, 2024]),
                      "acquired": how, "claimed_unit_cad": price, "luxury": True})

    pool = list(EVERYDAY)
    while len(lines) < 94:
        room, item, brand, model, (lo, hi), maxq = pool[(len(lines) - len(LUXURY)) % len(pool)]
        n = len(lines) - len(LUXURY)
        if n >= len(pool):  # second pass: variants so the list reaches 94
            item = f"{item} (second)"
        lines.append({"room": room, "item": item, "brand": brand, "model": model,
                      "qty": rnd.randint(1, maxq), "purchase_year": rnd.choice(range(2014, 2026)),
                      "acquired": "purchase", "claimed_unit_cad": round(rnd.uniform(lo, hi)),
                      "luxury": False})

    rnd.shuffle(lines)
    for n, ln in enumerate(lines, 1):
        ln["line"] = n
    # deliberate gaps an importer should flag
    lines[5]["qty"] = ""
    lines[17]["room"] = ""
    return lines


def photo_report(lines):
    """Contractor photo report: some luxury items branded, some probable, some absent."""
    lux = [ln for ln in lines if ln["luxury"]]
    rows, n = [], 1
    for k, ln in enumerate(lux):
        if k < 4:
            vis = "branded"
        elif k < 8:
            vis = "probable"
        else:
            continue  # last three luxury lines appear in no photo
        for _ in range(1 + (k % 2)):
            rows.append({"report": "Content damage report", "photo": n, "page": 1 + n // 7,
                         "visible": ln["item"], "branding": vis, "lines": ln["line"]})
            n += 1
    for ln in [x for x in lines if not x["luxury"]][:40]:
        rows.append({"report": "Content damage report", "photo": n, "page": 1 + n // 7,
                     "visible": ln["item"], "branding": "branded" if ln["brand"] else "none",
                     "lines": ln["line"]})
        n += 1
    for k in range(6):  # structure and drying photos, no contents line
        rows.append({"report": "Extraction report", "photo": k + 1, "page": 1,
                     "visible": "drying equipment, wet drywall", "branding": "none", "lines": ""})
    return rows


def documents(lines):
    lux = [ln for ln in lines if ln["luxury"]]
    docs = []
    for k, ln in enumerate(lux):
        if ln["acquired"] == "purchase" and k % 3 != 2:
            docs.append({"doc_id": f"D{len(docs) + 1:02d}", "file": f"card_statement_{k + 1}.pdf",
                         "type": "card_statement", "proves": "ownership,value", "lines": ln["line"]})
        if ln["acquired"] == "gift":
            docs.append({"doc_id": f"D{len(docs) + 1:02d}", "file": f"gift_letter_{k + 1}.pdf",
                         "type": "gift_letter", "proves": "ownership", "lines": ln["line"]})
    for ln in [x for x in lines if not x["luxury"] and x["claimed_unit_cad"] >= 800][:6]:
        docs.append({"doc_id": f"D{len(docs) + 1:02d}", "file": f"receipt_line_{ln['line']}.pdf",
                     "type": "receipt", "proves": "ownership,value", "lines": ln["line"]})
    return docs


def write():
    OUT.mkdir(parents=True, exist_ok=True)
    lines = build()
    cols = ["line", "room", "item", "brand", "model", "qty", "purchase_year", "acquired", "claimed_unit_cad"]
    with open(OUT / "lines.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(lines)
    photos = photo_report(lines)
    with open(OUT / "photo_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["report", "photo", "page", "visible", "branding", "lines"])
        w.writeheader()
        w.writerows(photos)
    docs = documents(lines)
    with open(OUT / "documents.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "file", "type", "proves", "lines"])
        w.writeheader()
        w.writerows(docs)

    def total(ln):
        return (ln["qty"] or 1) * ln["claimed_unit_cad"]

    line_sum = sum(total(ln) for ln in lines)
    lux_sum = sum(total(ln) for ln in lines if ln["luxury"])
    expected = {
        "lines": len(lines),
        "lineSumCad": line_sum,
        "statedTotalCad": line_sum + MISSING_TOTAL_OFFSET,
        "luxuryLines": [ln["line"] for ln in lines if ln["luxury"]],
        "luxurySumCad": lux_sum,
        "missingQtyLine": lines[5]["line"],
        "missingRoomLine": lines[17]["line"],
        "luxuryWithNoPhoto": [ln["line"] for ln in lines if ln["luxury"]][8:],
        "note": "Invented data. Missing qty is counted as 1 in lineSumCad.",
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


if __name__ == "__main__":
    e = write()
    print(f"{e['lines']} lines, lines sum ${e['lineSumCad']:,}, stated ${e['statedTotalCad']:,}, "
          f"luxury ${e['luxurySumCad']:,} ({e['luxurySumCad'] / e['lineSumCad']:.0%})")
