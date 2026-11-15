"""Support status per line: is ownership and value documented?

  supported    ownership and value both documented
  partial      one of the two documented
  unsupported  neither

Rules:
  * value needs a document: receipt, statement, invoice, order confirmation
    or appraisal; a photo never proves price
  * ownership needs a document for high-value lines (default CAD 1,000 per
    unit and up, which is where insurers ask for receipts, statements and
    certificates); below that, a photo of the item in the home counts toward
    ownership. The threshold is a setting, and Jason confirms it per claim
  * a gift letter proves ownership only
  * every status names what is missing, in words the policyholder can act on
  * the status describes paperwork, never honesty: it says nothing about
    whether the item existed
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import EvidenceRegister
from .records import ScheduleLine

HIGH_VALUE = 1000.0

SUPPORTED, PARTIAL, UNSUPPORTED = "supported", "partial", "unsupported"


@dataclass
class SupportResult:
    line_id: str
    status: str
    presence: list[str] = field(default_factory=list)     # photo refs
    ownership: list[str] = field(default_factory=list)    # doc ids or photo refs
    value: list[str] = field(default_factory=list)        # doc ids
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    high_value: bool = False


def line_support(line: ScheduleLine, reg: EvidenceRegister, high_value: float = HIGH_VALUE) -> SupportResult:
    photos = reg.photos_for(line.line_id)
    docs = reg.documents_for(line.line_id)
    hv = line.claimed_amount is not None and line.claimed_amount >= high_value

    presence = [p.ref + (" (branded)" if p.branding == "branded" else " (probable)" if p.branding == "probable"
                         else "") for p in photos]
    own = [d.doc_id for d in docs if "ownership" in d.proves]
    val = [d.doc_id for d in docs if "value" in d.proves]
    if not own and not hv and photos:
        own = [p.ref for p in photos]

    missing: list[str] = []
    if not own:
        missing.append("ownership: a receipt, card or bank statement, or gift letter from the giver"
                       if hv else "ownership: a photo of the item in the home, or a receipt or statement")
    if not val:
        missing.append("value: a receipt, card or bank statement, invoice, or appraisal")
    if hv and own and all(d.doc_type == "gift_letter" for d in docs if d.doc_id in own) and not val:
        missing[-1] = "value: the giver's purchase record (receipt or statement), or an appraisal"

    status = SUPPORTED if own and val else PARTIAL if own or val else UNSUPPORTED
    parts = []
    if own:
        parts.append("ownership " + ", ".join(own))
    if val:
        parts.append("value " + ", ".join(val))
    if presence:
        parts.append("seen in " + ", ".join(presence))
    reason = ("Documented: " + "; ".join(parts) + "." if parts else "No documents or photos tied to this line.")
    if missing:
        reason += " Missing " + "; ".join(missing) + "."
    if hv:
        reason += f" High-value line ({line.claimed_amount:,.2f} per unit): photos do not count toward ownership."
    return SupportResult(line.line_id, status, presence, own, val, missing, reason, hv)


def claim_support(lines: list[ScheduleLine], reg: EvidenceRegister,
                  high_value: float = HIGH_VALUE) -> list[SupportResult]:
    reg.check_lines({ln.line_id for ln in lines})
    return [line_support(ln, reg, high_value) for ln in lines]


def support_totals(lines: list[ScheduleLine], results: list[SupportResult]) -> dict:
    amount = {ln.line_id: (ln.claimed_amount or 0) * (ln.quantity or 1) for ln in lines}
    out = {s: {"lines": 0, "claimed": 0.0} for s in (SUPPORTED, PARTIAL, UNSUPPORTED)}
    for r in results:
        out[r.status]["lines"] += 1
        out[r.status]["claimed"] = round(out[r.status]["claimed"] + amount.get(r.line_id, 0), 2)
    return out
