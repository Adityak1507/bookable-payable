"""Pages -> documents. Decides what each PDF contains: 0..N payables and the pages that
are not payables (declined, with a reason).

Grouping is structural: a page with its own title and a different document number starts a
new document; an untitled page continues the current one; a page that repeats an earlier
page's line items is a second copy, not more lines; a page whose type is not payable
(delivery note, customs declaration, waybill, dunning letter, pro-forma…) is set aside.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from . import lexicon as lx
from .ir import DocIR, Party
from .numbers import money, near
from .understand import PageInfo
from .rules import rule

ZERO = Decimal(0)

_REASONS = {
    "PROFORMA": "pro-forma invoice: a quotation for payment in advance, not a demand for an amount owed",
    "DUNNING": "payment reminder: restates amounts already invoiced elsewhere; booking it would double-count",
    "DELIVERY_NOTE": "delivery note: records goods delivered, carries no amount owed",
    "CUSTOMS_DECLARATION": "customs declaration: supporting record of duties; the amount owed is billed by an invoice",
    "WAYBILL": "transport document (waybill): not a demand for payment",
    "STATEMENT": "statement of account: summarises invoices booked individually",
    "REMITTANCE": "remittance / payment advice slip: restates the amount of the invoice it accompanies",
    "ORDER": "order / quotation: not an amount owed",
    "FORM": "form: no payable content",
    "OTHER": "no payable content recognised on these pages",
}


@dataclass
class Group:
    doc_type: str
    pages: list[PageInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def number(self) -> str:
        return next((p.invoice_number for p in self.pages if p.invoice_number), "")


def _bills(p: PageInfo) -> bool:
    """An untitled page bills when it carries priced lines (money amounts), labelled total or not."""
    return (bool(p.lines) and bool(p.gross_candidates)) or \
        any(li.total and li.total.as_tuple().exponent <= -2 for li in p.lines)


def _signature(p: PageInfo) -> tuple:
    return tuple(li.total for li in p.lines)


@rule("assemble.grouping", "A titled page with a new number starts a document; an untitled page continues one; an untitled page that bills is a bill.")
@rule("assemble.second_copy", "A page repeating earlier line items is a second copy, not more lines.")
@rule("assemble.specification_page", "An untitled page whose amounts are all already billed is a specification, not more lines.")
def group_pages(pages: list[PageInfo]) -> list[Group]:
    groups: list[Group] = []
    for p in pages:
        dtype = p.doc_type
        if dtype == "OTHER" and _bills(p) and not (groups and groups[-1].doc_type in lx.PAYABLE_TYPES):
            dtype = "INVOICE"                       # an untitled page that bills is a bill
        cur = groups[-1] if groups else None
        starts_new = (
            cur is None
            or (dtype != "OTHER" and dtype != cur.doc_type)
            or (dtype != "OTHER" and p.invoice_number and cur.number and p.invoice_number != cur.number)
        )
        if starts_new:
            cur = Group(dtype)
            groups.append(cur)
        if cur.doc_type in lx.PAYABLE_TYPES and cur.pages and p.lines:
            sig = _signature(p)
            if any(_signature(q) == sig for q in cur.pages if q.lines):
                cur.notes.append(f"page {p.page_no} repeats the line items of an earlier page: second copy, not added")
                continue
            earlier = Counter(li.total for q in cur.pages for li in q.lines)
            if not (Counter(t for t in sig if t) - earlier) and p.doc_type == "OTHER":
                # every amount on this untitled page is already billed above: a specification
                # restating the lines, not more lines — keep the page for its totals only
                cur.notes.append(f"page {p.page_no} restates lines already billed (specification); lines not added")
                p = copy.copy(p)
                p.lines = []
        cur.pages.append(p)
    return groups


@rule("assemble.provable_gross", "The gross is the printed total the page's own components add up to.")
@rule("assemble.not_gross", "A zero sub-total or a count (Total 6) is not what is owed.")
def choose_gross(lines_total: Decimal, taxes: Decimal, charges: Decimal, discount: Decimal,
                 subtotal, total_tax, cands: list[tuple[int, Decimal, str]],
                 line_taxes: Decimal = ZERO) -> tuple[Decimal | None, str]:
    """The gross is the printed total that the page's own components add up to.
    Falls back to the most explicit 'amount due' label when no candidate is provable."""
    if not cands:
        return None, "no total printed"
    if any(v for _, v, _ in cands):
        cands = [c for c in cands if c[1]]        # a zero sub-total is not what is owed
    if any(v.as_tuple().exponent <= -2 for _, v, _ in cands):
        cands = [c for c in cands if c[1].as_tuple().exponent <= -2]   # 'Total 6' counts items, not money
    sums = {
        "lines + taxes + charges − discount": lines_total + taxes + charges - discount,
        "lines + their line taxes + charges − discount": lines_total + line_taxes + charges - discount,
        "lines + charges − discount (tax-inclusive prices)": lines_total + charges - discount,
    }
    if subtotal is not None and total_tax is not None:
        sums["printed subtotal + printed tax + charges"] = subtotal + total_tax + charges
    proven = [(pri, v, lab, how) for pri, v, lab in cands for how, s in sums.items() if near(v, s, Decimal("0.02"))]
    if proven:
        pri, v, lab, how = min(proven, key=lambda x: (x[0], -x[1]))
        return v, f"'{lab}' = {how}"
    pri, v, lab = min(cands, key=lambda x: (x[0], -x[1]))
    return v, f"'{lab}' (label only; components do not add up to any printed total)"


def _blank(p: PageInfo) -> PageInfo:
    q = copy.copy(p)
    q.lines, q.tax_rows, q.charges, q.gross_candidates, q.amounts = [], [], [], [], []
    q.header_discount = q.subtotal = q.total_tax = None
    return q


def _self_sufficient(pages: list[PageInfo]) -> bool:
    """Do these pages' own lines, taxes and charges add up to one of their printed totals?"""
    lines = [li for p in pages for li in p.lines]
    if not lines:
        return False
    lt = sum((li.total for li in lines if li.total is not None), ZERO)
    tx = sum(((-abs(t.amount) if t.withholding else t.amount) for p in pages for t in p.tax_rows
              if t.amount is not None), ZERO)
    ch = sum((c.amount for p in pages for c in p.charges if c.amount is not None), ZERO)
    disc = next((p.header_discount for p in pages if p.header_discount), ZERO)
    totals = {v for p in pages for _, v, _ in p.gross_candidates}
    return any(near(v, s, Decimal("0.02")) for v in totals for s in (lt + tx + ch - disc, lt + ch - disc))


@rule("assemble.single_currency", "A payable has one currency; pages restating it in another currency add nothing.")
def _single_currency(g: Group, notes: list[str]) -> Group:
    """A payable is in one currency. Pages of the same document in another currency (e.g. customs
    detail sheets in local currency) restate it; their amounts are not added."""
    cur, page_cur = "", {}
    for p in g.pages:
        cur = p.currency or cur                  # an unlabelled page continues the previous currency
        page_cur[p.page_no] = cur
    main = next((page_cur[p.page_no] for p in g.pages if p.gross_candidates and page_cur[p.page_no]), "")
    others = [p for p in g.pages if page_cur[p.page_no] and main and page_cur[p.page_no] != main]
    if not others:
        return g
    notes.append(f"pages {[p.page_no for p in others]} restate the document in "
                 f"{sorted({page_cur[p.page_no] for p in others})}; the payable is in {main}")
    blanked = []
    for p in g.pages:
        if p in others:
            q = _blank(p)
            q.currency = ""                      # a restatement does not vote on the payable's currency
            blanked.append(q)
        else:
            blanked.append(p)
    return Group(g.doc_type, blanked, g.notes)


@rule("assemble.backup_pages", "Untitled pages after an invoice that adds up on its own are backup, not lines.")
@rule("assemble.rate_total_restatement", "A rate-less tax equal to the rated taxes is their total, not another tax.")
@rule("assemble.unlabelled_gross", "An unlabelled printed amount the components reach is the gross, never over an explicit amount due.")
@rule("assemble.inferred_gross", "If no total is printed and no tax is stated, what is owed is the sum of printed lines (marked inferred).")
def to_ir(g: Group, source_file: str) -> tuple[DocIR, list[str]]:
    notes = list(g.notes)
    g = _single_currency(g, notes)
    titled = [p for p in g.pages if p.doc_type in lx.PAYABLE_TYPES]
    rest = [p for p in g.pages if p.doc_type not in lx.PAYABLE_TYPES]
    if titled and rest and any(p.lines or p.tax_rows or p.charges for p in rest) and _self_sufficient(titled):
        # the invoice pages already add up on their own: untitled pages after them are backup
        # (e.g. the utility bill being re-billed), not more lines
        notes.append(f"pages {[p.page_no for p in rest]} support the invoice (it adds up without them); "
                     "their amounts are not added")
        g = Group(g.doc_type, [p if p in titled else _blank(p) for p in g.pages], g.notes)
    first = lambda attr: next((getattr(p, attr) for p in g.pages if getattr(p, attr)), "")  # noqa: E731
    ir = DocIR(doc_type=g.doc_type, source_file=source_file, pages=[p.page_no for p in g.pages])
    ir.title = first("title")
    ir.text = "\n".join(p.text for p in g.pages)
    ir.invoice_number = g.number
    ir.invoice_date, ir.due_date = first("invoice_date"), first("due_date")
    ir.po_number, ir.payment_terms_text = first("po_number"), first("payment_terms_text")
    curs = [p.currency for p in g.pages if p.currency]
    ir.currency = max(set(curs), key=curs.count) if curs else ""
    ir.supplier = next((p.supplier for p in g.pages if p.supplier.vat_id or p.supplier.name), Party())
    ir.buyer = next((p.buyer for p in g.pages if p.buyer.name), Party())
    vat_ids = [v for p in g.pages for v in p.vat_ids]
    ibans = [v for p in g.pages for v in p.ibans]
    ir.supplier.extra_ids = vat_ids + ibans      # every printed identifier, for master lookup

    for p in g.pages:
        ir.lines.extend(p.lines)
    # A summary repeated on a later page restates it; two rows on the SAME page are two taxes.
    seen_tax: dict[tuple, int] = {}
    for p in g.pages:
        for t in p.tax_rows:
            key = (t.rate, t.amount, t.withholding)
            if seen_tax.get(key, p.page_no) != p.page_no or (t.rate is None and key in seen_tax):
                continue
            seen_tax.setdefault(key, p.page_no)
            ir.tax_rows.append(t)
        for c in p.charges:
            if not any(c.label == d.label and c.amount == d.amount and d.page != p.page_no for d in ir.charges):
                ir.charges.append(c)
        ir.header_discount = ir.header_discount or p.header_discount
        ir.subtotal = ir.subtotal if ir.subtotal is not None else p.subtotal
        ir.total_tax = ir.total_tax if ir.total_tax is not None else p.total_tax
        if p.prices_include_tax:
            ir.prices_include_tax = True
    rated = [t for t in ir.tax_rows if t.rate is not None and t.amount is not None]
    rated_sum = sum((t.amount for t in rated), ZERO)
    totals = [t for t in ir.tax_rows if t.rate is None and t.amount is not None and rated
              and near(abs(t.amount), abs(rated_sum), Decimal("0.02"))]
    for t in totals:
        ir.tax_rows.remove(t)
        ir.total_tax = ir.total_tax if ir.total_tax is not None else t.amount
        notes.append(f"'{t.name} {t.amount}' is the total of the rated taxes, not another tax")
    if any(li.price_basis == "gross" for li in ir.lines):
        ir.prices_include_tax = True

    lines_total = sum((li.total for li in ir.lines if li.total is not None), ZERO)
    taxes = sum(((-abs(t.amount) if t.withholding else t.amount) for t in ir.tax_rows if t.amount is not None), ZERO)
    charges = sum((c.amount for c in ir.charges if c.amount is not None), ZERO)
    line_taxes = sum(((li.tax_amount if li.tax_amount is not None else money(li.total * li.tax_rate / 100))
                      for li in ir.lines if li.total is not None and li.tax_rate is not None), ZERO)
    cands = [c for p in g.pages for c in p.gross_candidates]
    ir.gross, why = choose_gross(lines_total, taxes, charges, ir.header_discount or ZERO,
                                 ir.subtotal, ir.total_tax, cands, line_taxes)
    explicit_due = any(pri == 0 for p in g.pages for pri, _, _ in p.gross_candidates)
    if (ir.gross is None or "label only" in why) and ir.lines and not explicit_due:
        # no labelled total adds up; a printed (unlabelled) amount that the components reach is the
        # gross — never one the page labels as something else (subtotal, tax), never over 'Amount due'
        expect = {lines_total + taxes + charges - (ir.header_discount or ZERO),
                  lines_total + line_taxes + charges - (ir.header_discount or ZERO)}
        labelled_other = {ir.subtotal, ir.total_tax, *(t.amount for t in ir.tax_rows), *(t.base for t in ir.tax_rows)}
        hits = {v for p in g.pages for v in p.amounts
                if v and v not in labelled_other and any(near(v, e, Decimal("0.02")) for e in expect)}
        if len(hits) == 1:
            ir.gross = hits.pop()
            why = "an unlabelled printed amount equals lines + taxes + charges − discount"
    if ir.gross is None and ir.lines and not ir.tax_rows and line_taxes == 0:
        # the total field is printed blank (or absent); with no tax stated anywhere, what is owed
        # is what the printed lines add up to — recorded as inferred, not as printed
        ir.gross = lines_total + charges - (ir.header_discount or ZERO)
        ir.gross_inferred = True
        why = "no total printed; owed amount = sum of the printed line totals (inferred, no tax stated)"
    notes.append(f"gross {ir.gross}: {why}")
    # a total that the lines already reach is not a tax table restating them
    if ir.gross is not None and ir.tax_rows and near(lines_total + charges - (ir.header_discount or ZERO), ir.gross, Decimal("0.02")) \
            and not near(lines_total + taxes + charges, ir.gross, Decimal("0.02")):
        ir.prices_include_tax = True
        notes.append("printed lines already add up to the gross: prices include the stated tax")
    return ir, notes


@rule("assemble.remittance_number", "A remittance slip naming an invoice for the same amount supplies its number.")
def split(pages: list[PageInfo], source_file: str) -> tuple[list[tuple[DocIR, list[str]]], list[dict]]:
    payables, declined = [], []
    groups = group_pages(pages)
    slips = [(g.number, v) for g in groups if g.doc_type == "REMITTANCE" and g.number
             for p in g.pages for _, v, _ in p.gross_candidates]
    for g in groups:
        pages_txt = ", ".join(str(p.page_no) for p in g.pages)
        if g.doc_type in lx.PAYABLE_TYPES:
            ir, notes = to_ir(g, source_file)
            if not ir.lines and ir.gross is None:
                declined.append({"doc_type": g.doc_type, "reason": f"pages {pages_txt}: titled as a payable but carries no amounts"})
                continue
            if not ir.invoice_number and ir.gross is not None:
                hits = {n for n, v in slips if near(v, ir.gross)}
                if len(hits) == 1:
                    ir.invoice_number = hits.pop()
                    notes.append(f"invoice number {ir.invoice_number} taken from the remittance slip for the same amount")
            payables.append((ir, notes))
        else:
            declined.append({"doc_type": g.doc_type,
                             "reason": f"page(s) {pages_txt}: {_REASONS.get(g.doc_type, _REASONS['OTHER'])}",
                             "_text": "\n".join(p.text for p in g.pages)})     # for an optional second opinion
    return payables, declined
