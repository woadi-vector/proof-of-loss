"""The policyholder deliverable: one workbook an adjuster can review line by line.

Sheets:
  * Summary: claim totals, stated total and gap, support totals, how to read it
  * Inventory: one row per claimed line (layout below), with a totals row
  * Documents: every photo and document, what it proves, and its lines

Inventory columns: Line, Room, Item, Brand, Model, Qty, Age, Claimed each,
Claimed total, Replacement cost each, Claimed minus replacement each, Source
links (store, price, link, and any check note), Photo numbers, Ownership
document, Value document, Status, Notes. Replacement cost only: actual cash
value and depreciation are the carrier's to apply.

Rules:
  * replacement cost appears only with its cited listings; the average is
    computed by pricing.py, never typed in
  * every flag from import, pricing and support lands in Notes, so nothing is
    hidden in the tool
  * CSV copies are written beside the workbook for insurers that want plain files
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .claim_io import ImportResult
from .evidence import EvidenceRegister
from .pricing import Comparable, PriceResult, price_from_comps
from .support import SupportResult, support_totals

INVENTORY_COLUMNS = ["Line", "Room", "Item", "Brand", "Model", "Qty", "Age (years)", "Claimed each",
                     "Claimed total", "Replacement cost each", "Claimed minus replacement, each",
                     "Source links", "Photo numbers",
                     "Ownership document", "Value document", "Status", "Notes"]
DOCUMENT_COLUMNS = ["Ref", "Kind", "File or report", "Type", "Proves", "Lines", "SHA-256", "Note"]


def load_review(path: str | Path) -> dict[tuple[str, str], tuple[bool, str]]:
    """A person's decisions on found listings: line, url, use (yes/no), reason."""
    out: dict[tuple[str, str], tuple[bool, str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            use = str(row.get("use") or "").strip().lower() in ("yes", "y", "1", "true")
            out[(str(row.get("line") or "").strip(), (row.get("url") or "").strip())] = (
                use, (row.get("reason") or "").strip())
    return out


def load_comps(path: str | Path, review: dict[tuple[str, str], tuple[bool, str]] | None = None
               ) -> dict[str, list[Comparable]]:
    """Read hand-collected or found listings: line, source, url, price, currency, region, title, retrieved.

    With a review, only listings a person marked use=yes are read; a listing the review
    does not mention is left out, so nothing unreviewed reaches the workbook."""
    out: dict[str, list[Comparable]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lid = str(row.get("line") or "").strip()
            if review is not None:
                decision = review.get((lid, (row.get("url") or "").strip()))
                if not decision or not decision[0]:
                    continue
            try:
                price = float(str(row.get("price") or "").replace(",", "").replace("$", ""))
            except ValueError:
                price = 0.0
            out.setdefault(lid, []).append(Comparable(
                source=(row.get("source") or "").strip(), url=(row.get("url") or "").strip(), price=price,
                title=(row.get("title") or "").strip(), retrieved=(row.get("retrieved") or "").strip(),
                currency=(row.get("currency") or "").strip().upper(), region=(row.get("region") or "").strip().upper(),
                note=(row.get("note") or "").strip()))
    return out


@dataclass
class Workbook:
    inventory: list[list]
    documents: list[list]
    summary: list[list]


def build_rows(claim: ImportResult, reg: EvidenceRegister, support: list[SupportResult],
               comps: dict[str, list[Comparable]] | None = None, currency: str = "CAD",
               region: str | None = "CA", as_of: date | None = None) -> Workbook:
    as_of = as_of or date.today()
    comps = comps or {}
    sup = {s.line_id: s for s in support}
    issues: dict[str, list[str]] = {}
    for i in claim.issues:
        issues.setdefault(i.line_id, []).append(i.message)

    inv: list[list] = []
    priced_total = 0.0
    for il in claim.lines:
        ln = il.line
        s = sup.get(ln.line_id)
        pr: PriceResult | None = price_from_comps(comps[ln.line_id], currency, region) if ln.line_id in comps else None
        notes = list(issues.get(ln.line_id, []))
        if pr:
            notes += [f"pricing: {f}" for f in pr.flags]
            if pr.replacement_cost is not None:
                notes.append(f"replacement cost is the {pr.basis}")
                priced_total += pr.replacement_cost * (ln.quantity or 1)
        else:
            notes.append("no listings collected yet")
        if s and s.missing:
            notes += [f"missing {m}" for m in s.missing]
        if il.acquired == "gift":
            notes.append("gift")
        age = as_of.year - ln.purchase_year if ln.purchase_year else ""
        claimed_total = round(ln.claimed_amount * (ln.quantity or 1), 2) if ln.claimed_amount is not None else ""
        photos = [p for p in reg.photos_for(ln.line_id)]
        inv.append([
            ln.line_id, ln.room, ln.description, il.brand, il.model, ln.quantity, age,
            ln.claimed_amount if ln.claimed_amount is not None else "", claimed_total,
            pr.replacement_cost if pr and pr.replacement_cost is not None else "",
            round(ln.claimed_amount - pr.replacement_cost, 2)
            if pr and pr.replacement_cost is not None and ln.claimed_amount is not None else "",
            "\n".join(f"{c.source}: {c.price:,.2f} {c.currency} {c.url}" + (f" ({c.note})" if c.note else "")
                      for c in pr.sources) if pr else "",
            ", ".join(f"{p.report} #{p.photo}" for p in photos),
            ", ".join(s.ownership) if s else "", ", ".join(s.value) if s else "",
            s.status if s else "", "; ".join(notes)])

    docs: list[list] = []
    for d in reg.documents:
        docs.append([d.doc_id, "document", d.file, d.doc_type, ", ".join(sorted(d.proves)),
                     ", ".join(d.lines), d.sha256, d.note])
    for p in reg.photos:
        docs.append([p.ref, "photo", f"{p.report}, page {p.page}" if p.page else p.report,
                     p.branding, "presence", ", ".join(p.lines), "", p.visible])

    lines = [il.line for il in claim.lines]
    st = support_totals(lines, support)
    summ = [
        ["Contents claim workbook", ""],
        ["Prepared", as_of.isoformat()],
        ["Currency", currency],
        ["Lines", len(claim.lines)],
        ["Claimed, sum of lines", claim.computed_total],
        ["Claimed, stated total", claim.stated_total if claim.stated_total is not None else "not given"],
        ["Difference (lines minus stated)", claim.gap if claim.gap is not None else ""],
        ["Replacement cost, priced lines only", round(priced_total, 2)],
        ["Lines priced from listings", sum(1 for r in inv if r[9] != "")],
        ["", ""],
        ["Support", "Lines / Claimed"],
    ]
    for k in ("supported", "partial", "unsupported"):
        summ.append([k.capitalize(), f"{st[k]['lines']} / {currency} {st[k]['claimed']:,.2f}"])
    summ += [["", ""],
             ["How to read", "Supported: ownership and value documented. Partial: one of the two. "
              "Unsupported: neither. Photos show an item was present; they do not prove price. "
              "Notes on each line say what is missing."]]
    return Workbook(inv, docs, summ)


def write_csv(wb: Workbook, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, header, rows in (("inventory.csv", INVENTORY_COLUMNS, wb.inventory),
                               ("documents.csv", DOCUMENT_COLUMNS, wb.documents)):
        p = out_dir / name
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        paths.append(p)
    return paths


def write_xlsx(wb: Workbook, path: Path, currency: str = "CAD") -> Path:
    from openpyxl import Workbook as XL
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    x = XL()
    s = x.active
    s.title = "Summary"
    for row in wb.summary:
        s.append(row)
    s["A1"].font = Font(bold=True, size=13)
    s.column_dimensions["A"].width = 38
    s.column_dimensions["B"].width = 70
    for r in s.iter_rows(min_row=2):
        r[1].alignment = Alignment(wrap_text=True, vertical="top")

    inv = x.create_sheet("Inventory")
    inv.append(INVENTORY_COLUMNS)
    for row in wb.inventory:
        inv.append(row)
    n = len(wb.inventory)
    inv.append(["Total", "", "", "", "", "", "", "", f"=SUM(I2:I{n + 1})", "", "", "", "", "", "", ""])
    fills = {"supported": "C6EFCE", "partial": "FFEB9C", "unsupported": "FFC7CE"}
    for c in inv[1]:
        c.font = Font(bold=True)
    for r in range(2, n + 2):
        st = inv.cell(r, 16).value
        if st in fills:
            inv.cell(r, 16).fill = PatternFill("solid", fgColor=fills[st])
        for col in (8, 9, 10, 11):
            inv.cell(r, col).number_format = '#,##0.00'
        for col in (12, 17):
            inv.cell(r, col).alignment = Alignment(wrap_text=True, vertical="top")
    inv.cell(n + 2, 1).font = Font(bold=True)
    inv.cell(n + 2, 9).number_format = '#,##0.00'
    widths = [6, 18, 30, 16, 14, 5, 8, 12, 12, 14, 14, 60, 26, 16, 16, 12, 60]
    for i, w in enumerate(widths, 1):
        inv.column_dimensions[get_column_letter(i)].width = w
    inv.freeze_panes = "B2"
    inv.auto_filter.ref = f"A1:P{n + 1}"

    d = x.create_sheet("Documents")
    d.append(DOCUMENT_COLUMNS)
    for row in wb.documents:
        d.append(row)
    for c in d[1]:
        c.font = Font(bold=True)
    for i, w in enumerate([22, 10, 34, 18, 18, 14, 66, 40], 1):
        d.column_dimensions[get_column_letter(i)].width = w
    d.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    x.save(path)
    return path
