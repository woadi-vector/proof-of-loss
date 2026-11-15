"""Read a policyholder's claimed line list (CSV or XLSX) into schedule lines.

Real lists arrive in many shapes, so headers are matched loosely ("Qty",
"Quantity", "#"; "Unit Price", "Price Each"; "Total", "Line Total"; "Age",
"Year Purchased"). Nothing is guessed silently: every gap becomes an issue
the person can see, and the stated claim total is checked against what the
lines actually add up to.

Rules:
  * the source file is read, never written
  * a row whose item says "total" (and has no other item data) is the stated
    total, not a line
  * missing quantity is counted as 1 and flagged; missing price is left empty
    and flagged; nothing else is filled in
  * when only a line total is given, unit price = line total / quantity, flagged
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .records import ScheduleLine

ALIASES = {
    "line": ["line", "line #", "line no", "line number", "no", "#", "item #", "item no"],
    "room": ["room", "location", "area", "room/area"],
    "item": ["item", "description", "item description", "article", "item name", "contents"],
    "brand": ["brand", "make", "manufacturer", "designer"],
    "model": ["model", "model #", "model number", "style", "sku"],
    "qty": ["qty", "quantity", "count", "units"],
    "purchase_year": ["purchase year", "year purchased", "year bought", "purchase_year", "year"],
    "age": ["age", "age (years)", "age yrs", "years old"],
    "acquired": ["acquired", "how acquired", "purchase or gift", "source"],
    "unit_price": ["claimed_unit_cad", "unit price", "price each", "unit cost", "price", "replacement cost each",
                   "rcv each", "unit"],
    "line_total": ["total", "line total", "amount", "claimed", "claimed amount", "rcv", "replacement cost",
                   "total price"],
    "category": ["category", "type"],
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower())


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("CAD", "").replace("USD", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class ImportIssue:
    line_id: str          # "" for file-level issues
    field: str
    message: str


@dataclass
class ImportedLine:
    line: ScheduleLine
    acquired: str = ""    # purchase, gift, unknown, or as written
    source_row: int = 0   # row number in the file, header is row 1
    brand: str = ""
    model: str = ""


@dataclass
class ImportResult:
    lines: list[ImportedLine]
    issues: list[ImportIssue]
    columns: dict[str, str]                 # field -> header it was read from
    stated_total: float | None
    computed_total: float
    currency: str
    unpriced_lines: int = 0
    extra_headers: list[str] = field(default_factory=list)

    @property
    def gap(self) -> float | None:
        if self.stated_total is None:
            return None
        return round(self.computed_total - self.stated_total, 2)

    def summary(self) -> str:
        parts = [f"{len(self.lines)} lines, adding up to {self.currency} {self.computed_total:,.2f}"]
        if self.stated_total is not None:
            g = self.gap
            if g:
                parts.append(f"stated total {self.currency} {self.stated_total:,.2f} is off by "
                             f"{self.currency} {abs(g):,.2f} ({'under' if g > 0 else 'over'} the lines)")
            else:
                parts.append("stated total matches the lines")
        if self.unpriced_lines:
            parts.append(f"{self.unpriced_lines} line(s) have no price")
        parts.append(f"{len(self.issues)} issue(s) to review")
        return "; ".join(parts)


def _read_rows(path: Path) -> list[list]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def _map_headers(header: list) -> tuple[dict[str, int], list[str]]:
    found: dict[str, int] = {}
    names = [_norm(str(h) if h is not None else "") for h in header]
    for fld, aliases in ALIASES.items():
        for i, n in enumerate(names):
            if n in aliases and i not in found.values():
                found[fld] = i
                break
    extra = [str(header[i]) for i, n in enumerate(names) if n and i not in found.values()]
    return found, extra


def _is_total_row(cells: dict[str, object]) -> bool:
    item = str(cells.get("item") or "").strip().lower()
    return bool(item) and re.fullmatch(r"(grand |claim )?totals?:?", item) is not None


def load_claim(path: str | Path, stated_total: float | None = None, currency: str = "CAD",
               as_of: date | None = None) -> ImportResult:
    path = Path(path)
    as_of = as_of or date.today()
    rows = [r for r in _read_rows(path) if any(c not in (None, "") for c in r)]
    if not rows:
        return ImportResult([], [ImportIssue("", "file", "the file has no rows")], {}, stated_total, 0.0, currency)

    cols, extra = _map_headers(rows[0])
    header = rows[0]
    issues: list[ImportIssue] = []
    if "item" not in cols:
        issues.append(ImportIssue("", "item", "no item or description column found"))
    if "unit_price" not in cols and "line_total" not in cols:
        issues.append(ImportIssue("", "price", "no price or total column found"))

    lines: list[ImportedLine] = []
    found_stated = None
    total = 0.0
    unpriced = 0
    for r_i, row in enumerate(rows[1:], start=2):
        cells = {f: (row[i] if i < len(row) else None) for f, i in cols.items()}
        if _is_total_row(cells):
            found_stated = _num(cells.get("line_total")) or _num(cells.get("unit_price"))
            continue
        lid = str(cells.get("line") or len(lines) + 1).strip()
        if lid.endswith(".0"):
            lid = lid[:-2]

        def flag(fld: str, msg: str) -> None:
            issues.append(ImportIssue(lid, fld, msg))

        item = str(cells.get("item") or "").strip()
        if not item:
            flag("item", "no item description")
        room = str(cells.get("room") or "").strip()
        if "room" in cols and not room:
            flag("room", "no room given")
        brand = str(cells.get("brand") or "").strip()
        model = str(cells.get("model") or "").strip()
        if "brand" in cols and not brand:
            flag("brand", "no brand given")
        if "model" in cols and not model:
            flag("model", "no model given")

        q = _num(cells.get("qty"))
        if q is None or q <= 0:
            flag("qty", "no quantity given; counted as 1")
            q = 1.0
        qty = int(q) if float(q).is_integer() else q

        year = _num(cells.get("purchase_year"))
        age = _num(cells.get("age"))
        if year is None and age is not None:
            year = as_of.year - round(age)
        if year is None:
            flag("age", "no purchase year or age given")

        unit = _num(cells.get("unit_price"))
        lt = _num(cells.get("line_total"))
        if unit is None and lt is not None:
            unit = round(lt / q, 2)
            if q != 1:
                flag("price", f"unit price worked out from the line total ({lt:,.2f} / {qty})")
        if unit is None:
            unpriced += 1
            flag("price", "no price given")
        elif unit is not None and lt is not None and abs(unit * q - lt) > 0.5:
            flag("price", f"unit price x quantity ({unit * q:,.2f}) does not match the line total ({lt:,.2f})")

        if unit is not None:
            total += unit * q
        acquired = str(cells.get("acquired") or "").strip().lower()
        lines.append(ImportedLine(
            ScheduleLine(line_id=lid, room=room, description=item,
                         category=str(cells.get("category") or "").strip(),
                         brand_model=" ".join(x for x in (brand, model) if x),
                         quantity=qty, purchase_year=int(year) if year is not None else None,
                         claimed_amount=unit),
            acquired=acquired, source_row=r_i, brand=brand, model=model))

    ids = [ln.line.line_id for ln in lines]
    for d in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(ImportIssue(d, "line", "line number used more than once"))

    stated = stated_total if stated_total is not None else found_stated
    return ImportResult(lines, issues, {f: str(header[i]) for f, i in cols.items()}, stated,
                        round(total, 2), currency, unpriced, extra)
