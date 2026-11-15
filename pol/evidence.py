"""Evidence register: every photo and document, and the lines it supports.

Two kinds of evidence arrive with a contents claim:
  * photos in a contractor's report (Encircle or similar), each with a report
    name, photo number and page, and what is visible in it
  * documents the policyholder supplies: receipts, card or bank statements,
    gift letters, appraisals, packaging, certificates

Rules:
  * originals are referenced, never copied into the repo and never modified;
    when the files are on hand, each is hashed (sha256) so the register can
    show nothing changed
  * what a document proves comes from its type unless the register says
    otherwise: a gift letter proves ownership, not value
  * photos prove presence (the item was there and exposed to the loss); they
    do not prove authenticity or price
  * every reference to a line that is not in the claim is flagged
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .providers import file_sha256

# What each document type proves by default.
PROVES = {
    "receipt": {"ownership", "value"},
    "invoice": {"ownership", "value"},
    "card_statement": {"ownership", "value"},
    "bank_statement": {"ownership", "value"},
    "order_confirmation": {"ownership", "value"},
    "appraisal": {"value"},
    "gift_letter": {"ownership"},
    "packaging": {"ownership"},
    "authenticity_certificate": {"ownership"},
    "label_or_serial_photo": {"ownership"},
    "warranty_registration": {"ownership"},
    "manual": set(),
}

BRANDING = ("branded", "probable", "none")


@dataclass
class PhotoEvidence:
    report: str
    photo: str
    page: str = ""
    visible: str = ""
    branding: str = "none"          # branded, probable, none
    lines: list[str] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.report} #{self.photo}"


@dataclass
class DocumentEvidence:
    doc_id: str
    file: str
    doc_type: str
    proves: set[str]
    lines: list[str] = field(default_factory=list)
    sha256: str = ""                # filled when the file is on hand
    note: str = ""


@dataclass
class RegisterIssue:
    ref: str
    message: str


@dataclass
class EvidenceRegister:
    photos: list[PhotoEvidence] = field(default_factory=list)
    documents: list[DocumentEvidence] = field(default_factory=list)
    issues: list[RegisterIssue] = field(default_factory=list)

    def photos_for(self, line_id: str) -> list[PhotoEvidence]:
        return [p for p in self.photos if line_id in p.lines]

    def documents_for(self, line_id: str) -> list[DocumentEvidence]:
        return [d for d in self.documents if line_id in d.lines]

    def check_lines(self, known: set[str]) -> None:
        """Flag evidence that points at lines the claim does not have."""
        for p in self.photos:
            for ln in p.lines:
                if ln not in known:
                    self.issues.append(RegisterIssue(p.ref, f"points at line {ln}, which is not in the claim"))
        for d in self.documents:
            for ln in d.lines:
                if ln not in known:
                    self.issues.append(RegisterIssue(d.doc_id, f"points at line {ln}, which is not in the claim"))
            if not d.lines:
                self.issues.append(RegisterIssue(d.doc_id, "supports no line yet"))


def _split_lines(v: str) -> list[str]:
    return [x.strip() for x in str(v or "").replace(";", ",").split(",") if x.strip()]


def load_photo_report(path: str | Path, register: EvidenceRegister | None = None) -> EvidenceRegister:
    reg = register or EvidenceRegister()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            b = (row.get("branding") or "none").strip().lower()
            p = PhotoEvidence(report=(row.get("report") or "").strip(), photo=str(row.get("photo") or "").strip(),
                              page=str(row.get("page") or "").strip(), visible=(row.get("visible") or "").strip(),
                              branding=b if b in BRANDING else "none", lines=_split_lines(row.get("lines")))
            if b not in BRANDING:
                reg.issues.append(RegisterIssue(p.ref, f"branding '{b}' not understood; read as none"))
            reg.photos.append(p)
    return reg


def load_documents(path: str | Path, files_dir: str | Path | None = None,
                   register: EvidenceRegister | None = None) -> EvidenceRegister:
    """Read the document list. With `files_dir`, hash each file found there."""
    reg = register or EvidenceRegister()
    base = Path(files_dir) if files_dir else None
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            t = (row.get("type") or "").strip().lower()
            stated = {x.strip().lower() for x in (row.get("proves") or "").split(",") if x.strip()}
            proves = stated or PROVES.get(t, set())
            d = DocumentEvidence(doc_id=(row.get("doc_id") or "").strip(), file=(row.get("file") or "").strip(),
                                 doc_type=t, proves=proves, lines=_split_lines(row.get("lines")),
                                 note=(row.get("note") or "").strip())
            if t not in PROVES and not stated:
                reg.issues.append(RegisterIssue(d.doc_id, f"type '{t}' not known; proves nothing until set"))
            if t == "gift_letter" and "value" in proves:
                reg.issues.append(RegisterIssue(d.doc_id, "a gift letter is listed as proving value; "
                                                "value needs the giver's purchase record"))
            if base is not None:
                fp = base / d.file
                if fp.exists():
                    d.sha256 = file_sha256(fp)
                else:
                    reg.issues.append(RegisterIssue(d.doc_id, f"file {d.file} not found"))
            reg.documents.append(d)
    return reg
