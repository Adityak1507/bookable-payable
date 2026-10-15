"""Translate a faithful DocIR into the ERP's model.

The document is a computation (lines, taxes on some base, charges, restated summaries);
the ERP is a different, fixed computation (erp.py). This module enumerates the small,
finite set of ways the ERP can be told the document's components — tax on the line or at
the header, prices net or tax-inclusive — books each one with the sealed erp.py, and keeps
the most faithful structure that books the printed gross.

Two invariants make it safe:
  * values only come from the document (or from a rule the document itself proves, e.g. a
    net derived from an inclusive price whose split reproduces the printed tax);
  * the printed gross is used to CHOOSE between structures, never to adjust a value.
    If no structure books it, the record is emitted as-is and flagged — never plugged.
"""
from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from .ir import DocIR, Line, TaxRow
from .numbers import fmt, fmt_money, money, near

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from erp import erp_book  # noqa: E402  — the sealed recompute, imported unchanged
from .rules import rule

ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass
class Fact:
    """A provable statement about the document, with the field it concerns."""
    kind: str
    message: str
    field: str = ""


@dataclass
class Candidate:
    name: str
    payable: dict
    booked: Decimal
    exact: bool
    penalty: int
    derived: int
    facts: list[Fact] = field(default_factory=list)


@dataclass
class Result:
    payable: dict
    reconciled: bool
    booked: Decimal
    target: Decimal | None
    chosen: str
    facts: list[Fact]
    alternatives: list[tuple[str, str, bool]]


# ── sign normalisation ──────────────────────────────────────────────────────

@rule("solve.credit_positive", "A credit printed negative is delivered as positive magnitudes.")
def normalise_sign(ir: DocIR, facts: list[Fact]) -> DocIR:
    """A credit printed with negative figures is delivered as positive magnitudes.
    Flip is decided once, from the printed gross, and applied to every money value."""
    if ir.gross is None or ir.gross >= 0:
        return ir
    ir = copy.deepcopy(ir)
    neg = lambda d: None if d is None else -d          # noqa: E731
    for li in ir.lines:
        li.unit_price, li.total, li.tax_amount = neg(li.unit_price), neg(li.total), neg(li.tax_amount)
    for t in ir.tax_rows:
        t.amount, t.base = neg(t.amount), neg(t.base)
    for c in ir.charges:
        c.amount = neg(c.amount)
    ir.gross, ir.subtotal, ir.total_tax = neg(ir.gross), neg(ir.subtotal), neg(ir.total_tax)
    if ir.doc_type == "INVOICE":
        ir.doc_type = "CREDIT_NOTE"
    facts.append(Fact("sign", "printed gross is negative: credit delivered as positive magnitudes", "gross_total"))
    return ir


# ── lines: what quantity × price the ERP should see ─────────────────────────

@dataclass
class PreparedLine:
    src: Line
    quantity: Decimal
    unit_price: Decimal
    discount_pct: Decimal | None
    discount_amount: Decimal | None
    derived: int = 0


def _erp_extension(q: Decimal, p: Decimal, pct, amt) -> Decimal:
    sub = q * p
    if pct:
        return money(sub - sub * abs(pct) / HUNDRED)
    if amt and q:
        return money((p - abs(amt) / abs(q)) * q)
    return money(sub)


@rule("solve.price_scale", "A price per 10/100/1000 units is proven by the printed line total.")
@rule("solve.keep_printed_quantity", "With no unit price printed, keep the printed quantity and the price that reproduces the total.")
def prepare_line(li: Line, facts: list[Fact], i: int) -> PreparedLine:
    q, p, tot = li.quantity, li.unit_price, li.total
    pct, amt = li.discount_pct, li.discount_amount
    if q is None and p is None and tot is not None:
        return PreparedLine(li, Decimal(1), tot, None, None)
    if q is None:
        q = Decimal(1)
    if p is None:
        if tot is None:
            return PreparedLine(li, q, ZERO, pct, amt)
        if q == 1:
            return PreparedLine(li, q, tot, pct, amt)
        if q:
            # no unit price printed: keep the printed quantity and the price that reproduces the
            # printed extension exactly (recorded as derived)
            price = (tot / q).quantize(Decimal("0.000001")).normalize()
            if money(q * price) == tot:
                facts.append(Fact("derived", f"line {i}: no unit price printed; {fmt(price)} = printed total "
                                  f"{tot} / quantity {fmt(q)}", f"line_items[{i}].unit_price"))
                return PreparedLine(li, q, price, pct, amt, derived=1)
        return PreparedLine(li, Decimal(1), tot, None, None)
    if tot is None or near(_erp_extension(q, p, pct, amt), tot, Decimal("0.01")):
        return PreparedLine(li, q, p, pct, amt)
    # Printed total contradicts qty × price: is the price quoted per pack of 10^k units?
    for k in (10, 100, 1000):
        if near(_erp_extension(q, p / k, pct, amt), tot, Decimal("0.01")):
            facts.append(Fact("scale", f"line {i}: price is per {k} units (qty × price / {k} = printed total)",
                              f"line_items[{i}].unit_price"))
            return PreparedLine(li, q, p / k, pct, amt, derived=1)
    # A discount column the parser did not capture explains it?
    if pct is None and amt is None and q * p and tot < q * p:
        facts.append(Fact("line_mismatch", f"line {i}: qty × price = {money(q * p)} but printed total {tot}; "
                          "keeping printed total as the extension", f"line_items[{i}]"))
    else:
        facts.append(Fact("line_mismatch", f"line {i}: qty × price ≠ printed total ({money(q * p)} vs {tot})",
                          f"line_items[{i}]"))
    return PreparedLine(li, Decimal(1), tot, None, None, derived=0)


# ── payable assembly ─────────────────────────────────────────────────────────

def _tax(name: str, rate, amount, ttype: str = "") -> dict:
    return {"tax_type": ttype, "tax_name": name, "tax_rate": fmt(rate) if rate is not None else "",
            "tax_amount": fmt_money(amount) if amount is not None else "", "tax_type_code": ""}


def _line_dict(pl: PreparedLine, unit_price: Decimal, taxes: list[dict]) -> dict:
    li = pl.src
    return {
        "description": li.description, "item_type": li.kind or "", "uom": li.uom,
        "quantity": fmt(pl.quantity), "unit_price": fmt(unit_price),
        "total": fmt_money(li.total) if li.total is not None else "",
        "discount": fmt_money(pl.discount_amount) if pl.discount_amount else "",
        "discount_percentage": fmt(pl.discount_pct) if pl.discount_pct else "",
        "tax_rate": "", "tax_amount": "", "taxes": taxes,
    }


def _header(ir: DocIR) -> dict:
    buckets = {"freight": ZERO, "insurance": ZERO, "extra": ZERO, "excise": ZERO}
    seen = {k: False for k in buckets}
    for c in ir.charges:
        if c.amount is not None:
            buckets[c.bucket] += c.amount
            seen[c.bucket] = True
    ch = lambda k: fmt_money(buckets[k]) if seen[k] else ""   # noqa: E731
    return {
        "invoice_number": ir.invoice_number, "invoice_date": ir.invoice_date, "due_date": ir.due_date,
        "invoice_type": "CREDIT_MEMO" if ir.doc_type == "CREDIT_NOTE" else "INVOICE",
        "currency": ir.currency,
        "supplier": {"name": ir.supplier.name, "supplier_id": "", "address": ir.supplier.address,
                     "vat_id": ir.supplier.vat_id},
        "buyer": {"company_code": "", "business_unit_code": "", "location_code": ""},
        "payment_term_id": "", "po_number": ir.po_number, "po_id": "",
        "gross_total": fmt_money(ir.gross), "subtotal": fmt_money(ir.subtotal),
        "total_tax_amount": fmt_money(ir.total_tax),
        "discount_amount": fmt_money(ir.header_discount) if ir.header_discount else "",
        "freight_charges": ch("freight"), "insurance_charges": ch("insurance"),
        "extra_charges": ch("extra"), "excise_duties": ch("excise"),
        "taxes": [], "line_items": [],
    }


def _row_amount(t: TaxRow):
    if t.amount is None:
        return None
    return -abs(t.amount) if t.withholding else t.amount


def _line_has_tax(li: Line) -> bool:
    return li.tax_rate is not None or li.tax_amount is not None


def _uncovered_rows(ir: DocIR, line_rates: set) -> list[TaxRow]:
    """Header rows that are NOT a restatement of line taxes (a withholding, or a levy at a
    rate no line carries) — they stay at the header when taxes are placed on the lines."""
    return [t for t in ir.tax_rows
            if t.withholding or (t.rate is not None and t.rate not in line_rates)]


def build(ir: DocIR, prepared: list[PreparedLine], tax_mode: str, price_mode: str) -> tuple[dict, int] | None:
    """One ERP structure. Returns (payable, n_derived) or None if not applicable."""
    p = _header(ir)
    derived = sum(pl.derived for pl in prepared)
    line_taxed = any(_line_has_tax(pl.src) for pl in prepared)
    single_rate = None
    rated_rows = [t for t in ir.tax_rows if not t.withholding and t.rate is not None]
    if len(rated_rows) == 1:
        single_rate = rated_rows[0].rate

    if tax_mode == "line" and not line_taxed:
        return None
    if tax_mode == "header" and not ir.tax_rows:
        return None

    used_rates: set = set()
    for pl in prepared:
        li, price = pl.src, pl.unit_price
        rate = li.tax_rate if li.tax_rate is not None else single_rate
        if rate is not None and (tax_mode == "inclusive_line" or li.tax_rate is not None):
            used_rates.add(rate)
        if price_mode == "inclusive":
            if rate is None:
                return None
            price = (price / (1 + rate / HUNDRED)).quantize(Decimal("0.000001"))
            derived += 1
        taxes = []
        if tax_mode == "line" and _line_has_tax(li):
            taxes = [_tax(li.tax_name or "VAT", li.tax_rate, li.tax_amount)]
        elif tax_mode == "inclusive_line":
            taxes = [_tax(li.tax_name or "VAT", rate, None)] if rate is not None else []
        p["line_items"].append(_line_dict(pl, price, taxes))

    if tax_mode == "header":
        p["taxes"] = [_tax(t.name, t.rate, _row_amount(t)) for t in ir.tax_rows]
    elif tax_mode in ("line", "inclusive_line"):
        p["taxes"] = [_tax(t.name, t.rate, _row_amount(t)) for t in _uncovered_rows(ir, used_rates)]
    return p, derived


def _placement_penalty(ir: DocIR, tax_mode: str, price_mode: str) -> int:
    line_taxed = any(_line_has_tax(li) for li in ir.lines)
    has_tax = bool(ir.tax_rows) or line_taxed
    if price_mode == "inclusive":
        return 1 if ir.prices_include_tax else 4          # derivation must be proven by the print
    if tax_mode == "none":
        if not has_tax:
            return 0
        return 3 if ir.prices_include_tax else 6          # inclusive booked as printed, tax unrepresented
    if tax_mode == "line":
        return 0
    if tax_mode == "header":
        return 2 if line_taxed else 0                      # policy B: printed per-rate summary
    return 5


MODES = [("line", "net"), ("header", "net"), ("inclusive_line", "inclusive"), ("none", "net")]


@rule("solve.search_structures", "Try each ERP structure, book it with erp.py, keep the most faithful one that books the printed gross.")
@rule("solve.no_plug", "If no structure books the gross, emit the most faithful record and flag it; never adjust a value.")
@rule("solve.rounding_policy_b", "If line-level tax drifts from printed per-rate amounts, the printed amounts go at the header.")
@rule("solve.inclusive_proven", "Net prices are derived from inclusive ones only when the split reproduces a printed tax.")
def solve(ir: DocIR) -> Result:
    facts: list[Fact] = []
    ir = normalise_sign(ir, facts)
    prepared = [prepare_line(li, facts, i) for i, li in enumerate(ir.lines)]
    _tax_base_facts(ir, prepared, facts)

    cands: list[Candidate] = []
    for tax_mode, price_mode in MODES:
        built = build(ir, prepared, tax_mode, price_mode)
        if built is None:
            continue
        payable, derived = built
        booked = Decimal(str(erp_book(payable)["will_book_gross"]))
        exact = ir.gross is not None and near(booked, ir.gross)
        if price_mode == "inclusive" and exact:
            # the derivation is only allowed when its split reproduces a PRINTED tax figure
            printed_tax = ir.total_tax
            if printed_tax is None and ir.tax_rows and all(t.amount is not None for t in ir.tax_rows):
                printed_tax = sum((t.amount for t in ir.tax_rows if not t.withholding), ZERO)
            if printed_tax is None or not near(booked - _untaxed(payable), printed_tax, Decimal("0.01")):
                exact = False
        cands.append(Candidate(f"{tax_mode}/{price_mode}", payable, booked, exact,
                               _placement_penalty(ir, tax_mode, price_mode), derived))

    if not cands:
        raise ValueError("no line items or taxes to book")
    best = min(cands, key=lambda c: (not c.exact, c.penalty, c.derived))
    if not best.exact:
        best = min(cands, key=lambda c: (c.penalty, c.derived))   # most faithful; flagged
        facts.extend(_diagnose(ir, best))
    elif best.name != "line/net" and any(c.name == "line/net" for c in cands):
        ln = next(c for c in cands if c.name == "line/net")
        facts.append(Fact("choice", f"chose {best.name}: line-level taxes book {ln.booked}, "
                          f"printed gross {ir.gross} (delta {ln.booked - ir.gross})"))
    return Result(best.payable, best.exact, best.booked, ir.gross, best.name, facts,
                  [(c.name, str(c.booked), c.exact) for c in cands])


def _untaxed(payable: dict) -> Decimal:
    """What the ERP books for the same payable with every tax removed."""
    stripped = copy.deepcopy(payable)
    stripped["taxes"] = []
    for li in stripped["line_items"]:
        li["taxes"], li["tax_rate"], li["tax_amount"] = [], "", ""
    return Decimal(str(erp_book(stripped)["will_book_gross"]))


# ── facts about the document's own arithmetic ───────────────────────────────

def _tax_base_facts(ir: DocIR, prepared: list[PreparedLine], facts: list[Fact]) -> None:
    """For each printed tax with a rate and an amount, find which base the document used."""
    net = sum((_erp_extension(pl.quantity, pl.unit_price, pl.discount_pct, pl.discount_amount)
               for pl in prepared), ZERO) - (ir.header_discount or ZERO)
    levies = [t for t in ir.tax_rows if t.amount is not None and not t.withholding]
    charges = sum((c.amount for c in ir.charges if c.amount is not None), ZERO)
    for i, t in enumerate(ir.tax_rows):
        if t.rate is None or t.amount is None or t.rate == 0:
            continue
        others = sum((o.amount for o in levies if o is not t and o.rate is not None and o.rate < t.rate), ZERO)
        bases = [("net", net), ("printed base", t.base), ("net + other levies", net + others),
                 ("net + charges", net + charges)]
        hit = next((n for n, b in bases if b is not None and near(money(b * t.rate / HUNDRED), abs(t.amount), Decimal("0.01"))), None)
        if hit and hit != "net":
            facts.append(Fact("tax_base", f"{t.name or 'tax'} {fmt(t.rate)}% is computed on {hit}, "
                              "so its printed amount is given explicitly", f"taxes[{i}].tax_amount"))
        elif hit is None and t.base is None:
            facts.append(Fact("tax_base", f"{t.name or 'tax'} {fmt(t.rate)}% × net ≠ printed {t.amount}; "
                              "base not provable from the page", f"taxes[{i}]"))


def _diagnose(ir: DocIR, c: Candidate) -> list[Fact]:
    if ir.gross is None:
        return [Fact("unreconciled", "document prints no gross to reconcile against", "gross_total")]
    delta = c.booked - ir.gross
    msg = f"no ERP structure books the printed gross: best faithful structure {c.name} books {c.booked}, " \
          f"document says {ir.gross} (delta {delta})"
    out = [Fact("unreconciled", msg, "gross_total")]
    if ir.total_tax and near(abs(delta), ir.total_tax, Decimal("0.02")):
        out.append(Fact("delta", "delta equals the printed total tax: tax counted twice or prices already include it"))
    elif abs(delta) <= Decimal("0.01") * max(1, len(ir.lines)):
        out.append(Fact("delta", "delta is within per-line rounding; the document rounds tax at a level the ERP cannot express"))
    for ch in ir.charges:
        if ch.amount is not None and near(abs(delta), abs(ch.amount), Decimal("0.01")):
            out.append(Fact("delta", f"delta equals charge '{ch.label}'"))
    return out
