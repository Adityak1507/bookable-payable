"""The totals block: label → value entries, tax rows, charges, deductions, gross candidates, the cascade."""
from __future__ import annotations

import re
from decimal import Decimal

from .. import lexicon as lx
from ..ir import Charge, TaxRow
from ..numbers import money, near, parse_amount
from ..ocr.layout import TextLine
from .cells import HUNDRED, Cell, cells_of, num, is_code, _is_currency
from .page import PageInfo
from ..rules import rule


@rule("summary.most_specific_label", "When a label matches several kinds, the longest phrase decides (IVA Total is a tax total).")
@rule("summary.typo_tolerant_labels", "Total and withholding labels tolerate OCR typos ((OTAL PAYMEN)).")
@rule("summary.component_total", "A total naming a component (excise, freight, discount) totals that component, not the document.")
def _summary_label(text: str) -> str | None:
    # the most specific (longest) matching phrase decides: 'IVA Total' is a tax total, not a total
    hits = [(len(p), kind) for kind, phrases in (("gross", lx.GROSS), ("subtotal", lx.SUBTOTAL),
                                                  ("total_tax", lx.TOTAL_TAX), ("withholding", lx.WITHHOLDING))
            for p in [lx.has(text, phrases)] if p]
    if hits:
        kind = max(hits)[1]
        if kind == "gross" and lx.has(text, lx.EXCISE + lx.FREIGHT + lx.INSURANCE + lx.DISCOUNT)                 and max(hits)[0] <= 6:
            return None                    # 'Total Valor IEC' totals the excise, not the document
        return kind
    for kind, phrases in (("withholding", lx.WITHHOLDING), ("gross", lx.GROSS)):
        if lx.has(text, phrases, fuzzy=True):         # OCR-damaged spellings of the same labels
            return kind
    return None


_GROSS_PRIORITY = {p: (0 if any(k in p for k in ("due", "pay", "tasuda", "zahl", "pagar", "betale", "payer"))
                       else 1 if any(k in p for k in ("grand", "invoice total", "rechnungsbetrag", "gesamtbetrag",
                                                      "inclusive", "incl", "kokku tasuda", "total amount"))
                       else 2) for p in lx.GROSS}


@rule("summary.label_left", "A bare value takes the label just left of it on a slightly offset line.")
def _label_left(lines: list[TextLine], i: int, c: Cell, dec) -> str:
    """A label printed on the same visual row but slightly off the value's baseline
    ('TOTAL ........ EUR 153,58' grouped as two lines)."""
    h = lines[i].height
    cy = (lines[i].y0 + lines[i].y1) / 2
    for j in (i - 1, i + 1):
        if not 0 <= j < len(lines):
            continue
        cj = (lines[j].y0 + lines[j].y1) / 2
        if abs(cj - cy) > 0.9 * h:
            continue
        cells = cells_of(lines[j])
        if any(num(d.text, dec) is not None and d.x0 > c.x1 for d in cells):
            continue
        left = [d for d in cells if d.x1 <= c.x0 and num(d.text, dec) is None and not _is_currency(d.text)]
        if left and _summary_label(left[-1].text):
            return left[-1].text
    return ""


@rule("summary.label_above", "A row of bare values takes, per value, the label above it, never from a label+value row.")
def _label_above(lines: list[TextLine], i: int, c: Cell, dec) -> str:
    """The text cell printed directly above a value (labels stacked over a row of totals)."""
    for j in range(i - 1, max(-1, i - 4), -1):
        if lines[i].y0 - lines[j].y1 > 3 * lines[i].height:
            break
        above = cells_of(lines[j])
        if any(num(d.text, dec) is not None for d in above):
            continue                     # a label + value row, not a column header for what is below
        best = None
        for d in above:
            if num(d.text, dec) is not None or _is_currency(d.text):
                continue
            ov = min(d.x1, c.x1) - max(d.x0, c.x0)
            if ov > 0 and (best is None or ov > best[0]):
                best = (ov, d.text)
        if best:
            return best[1]
    return ""


@rule("summary.side_by_side_pairs", "Several label -> value pairs on one line are separate entries.")
def _label_value_segments(cells: list[Cell], dec) -> list[tuple[str, list, str]]:
    """Split a line into [label cells][value cells] segments ('Desc. Prom.: 1.049,60 |
    Sub-Total c/ IVA: 1.012,14' is two entries, not one)."""
    segs, label, vals = [], [], []
    for c in cells:
        v = num(c.text, dec)
        if v is None and not _is_currency(c.text):
            if vals:
                segs.append((label, vals))
                label, vals = [], []
            label.append(c.text)
        elif v is not None and not is_code(c.text):
            vals.append((c, v))
    if vals:
        segs.append((label, vals))
    segs = [(" ".join(l), v) for l, v in segs if l and re.search(r"[A-Za-z]{2}", " ".join(l))]
    return [(l, v, l + " " + " ".join(c.text for c, _ in v)) for l, v in segs]


def summary_entries(lines: list[TextLine], dec, skip: range) -> list[tuple[str, list, str]]:
    """(label, [(cell, value)], text) for each amount-bearing line outside the item table.
    A row of bare numbers takes, per number, the label printed directly above it."""
    out = []
    for i, ln in enumerate(lines):
        if i in skip:
            continue
        cells = cells_of(ln)
        vals = [(c, num(c.text, dec)) for c in cells]
        vals = [(c, v) for c, v in vals if v is not None and not is_code(c.text)]
        if not vals:
            continue
        label = " ".join(c.text for c in cells if num(c.text, dec) is None and not _is_currency(c.text))
        if re.search(r"[A-Za-z]{2}", label):
            segs = _label_value_segments(cells, dec)
            if len(segs) > 1:              # several label → value pairs side by side on one line
                out.extend(segs)
            else:
                out.append((label, vals, ln.text))
            continue
        if any("%" in c.text for c, _ in vals) and len(vals) >= 3:     # a row of a tax-rate table
            above = _label_above(lines, i, vals[0][0], dec)
            out.append((above, vals, ln.text))
            continue
        for c, v in vals:
            lab = _label_left(lines, i, c, dec) or _label_above(lines, i, c, dec)
            if lab:
                out.append((lab, [(c, v)], f"{lab} {c.text}"))
    return out


@rule("summary.cascade", "A total that later rows keep adding to is a step of the cascade, not what is owed.")
def extract_summary(info: PageInfo, lines: list[TextLine], dec, skip: range) -> None:
    seq: list[tuple[str, int, Decimal]] = []    # page order of totals, components and printed amounts
    for label, vals, txt in summary_entries(lines, dec, skip):
        before = (len(info.tax_rows), len(info.charges), len(info.gross_candidates), info.header_discount)
        _summary_entry(info, label, vals, txt, dec)
        if len(info.gross_candidates) > before[2]:
            seq.append(("total", len(info.gross_candidates) - 1, info.gross_candidates[-1][1]))
        else:
            added = [(-abs(t.amount) if t.withholding else t.amount) for t in info.tax_rows[before[0]:]
                     if t.amount is not None] + [c.amount for c in info.charges[before[1]:] if c.amount is not None]
            disc = info.header_discount if info.header_discount != before[3] and info.header_discount else None
            if any(a for a in added) or disc:
                seq.append(("component", -1, sum(added, Decimal(0)) - (disc or 0)))   # zero rows are not steps
            else:
                seq.extend(("amount", -1, v) for c, v in vals
                           if re.search(r"\d[.,]\d{2}(?!\d)", c.text) and "%" not in c.text)
    # A total that later rows keep adding to (fee, tax, withholding) is an intermediate step of the
    # cascade, not what is owed: proven by a later total, or by a later printed amount that equals
    # it plus the components in between, whatever that amount's label says.
    demote = set()
    for k, (kind, i, v) in enumerate(seq):
        if kind != "total":
            continue
        running, stepped = v, False
        for later_kind, _, lv in seq[k + 1:]:
            if later_kind == "component":
                running, stepped = running + lv, True
            elif stepped and (later_kind == "total" or near(lv, running, Decimal("0.02"))):
                demote.add(i)
                break
    if demote:
        info.intermediate_totals = [c[1] for i, c in enumerate(info.gross_candidates) if i in demote]
        info.gross_candidates = [c for i, c in enumerate(info.gross_candidates) if i not in demote]


@rule("summary.rate_table", "A tax row is recognised by rate x base = amount, in either printed order.")
@rule("summary.deduction_after_tax", "Amounts paid or credited after tax reduce what is owed (a negative untaxed charge).")
@rule("summary.excl_is_subtotal", "Amount excl. tax is a subtotal, not a tax.")
@rule("summary.tax_base_not_tax", "A tax base (Incidencia, KM alus) restates the net; it is not a tax.")
def _summary_entry(info: PageInfo, label: str, vals: list, txt: str, dec) -> None:
    info.amounts.extend(v for c, v in vals if re.search(r"\d[.,]\d{2}(?!\d)", c.text) and "%" not in c.text)
    rate = None
    m = re.search(r"(\d{1,2}(?:[.,]\d{1,2})?)\s?%", txt) or re.search(r"(?<![\d.,])%\s?(\d{1,2})(?![\d.,])", txt)
    if m:
        rate = parse_amount(m.group(1), dec if dec == "," else ".")
    money_vals = [v for c, v in vals if "%" not in c.text]
    if not money_vals:
        return
    amount = money_vals[-1]
    kind = _summary_label(label)
    if kind is None and rate is None and lx.has(label, lx.TAX_BASE):
        return                                   # a tax base restates the net, it is not a tax
    if lx.has(label, lx.INCL_HINT) and kind in ("subtotal", None) and not lx.has(label, lx.GROSS[:20]):
        info.prices_include_tax = True

    # rate table row: "24%  104 263.02  25 023.12  129 286.14" (rate × base = amount)
    if rate is not None and len(money_vals) >= 2:
        for a, b in zip(money_vals, money_vals[1:]):
            # base then tax ('24% 104 263.02 25 023.12') or tax then base ('VAT @16% 9,745.42 60,908.88')
            base, amt = (a, b) if near(money(a * rate / HUNDRED), b, Decimal("0.02")) else \
                (b, a) if near(money(b * rate / HUNDRED), a, Decimal("0.02")) else (None, None)
            if base is not None:
                name = lx.has(label, lx.TAX_WORDS) or "VAT"
                info.tax_rows.append(TaxRow(name.upper(), rate, base=base, amount=amt,
                                            withholding=bool(lx.has(label, lx.WITHHOLDING, fuzzy=True))))
                break
        else:
            if kind == "gross" or kind == "subtotal":
                pass
            elif lx.has(label, lx.TAX_WORDS) and kind != "total_tax":
                info.tax_rows.append(TaxRow(_tax_name(label), rate, amount=amount,
                                            withholding=bool(lx.has(label, lx.WITHHOLDING, fuzzy=True))))
                return
        if kind not in ("gross", "subtotal", "total_tax"):
            return
    if kind == "gross":
        p = lx.has(label, lx.GROSS)
        info.gross_candidates.append((_GROSS_PRIORITY.get(p, 2), amount, label.strip()))
        return
    if kind == "subtotal":
        info.subtotal = info.subtotal if info.subtotal is not None else amount
        if lx.has(label, lx.INCL_HINT):
            info.prices_include_tax = True
        return
    if kind == "total_tax":
        info.total_tax = amount
        if rate is not None:          # 'Käibemaks 24%  117,72' is the tax itself, with its rate
            info.tax_rows.append(TaxRow(_tax_name(label), rate, amount=amount,
                                        withholding=bool(lx.has(label, lx.WITHHOLDING, fuzzy=True))))
        return
    money_fmt = bool(re.search(r"\d[.,]\d{2}(?!\d)", vals[-1][0].text))   # a charge is a money amount
    if lx.has(label, lx.DEDUCTION) and len(label) < 45 and money_fmt:
        if amount:                       # paid / credited after tax: reduces what is owed
            info.charges.append(Charge(label.strip(), -abs(amount), "extra", info.page_no))
        return
    if kind is None and rate is None and lx.has(label, lx.EXCL_HINT):
        info.subtotal = info.subtotal if info.subtotal is not None else amount   # 'Amount Excl Tax'
        return
    if kind == "withholding" or (lx.has(label, lx.TAX_WORDS) and len(label) < 60):
        info.tax_rows.append(TaxRow(_tax_name(label), rate, amount=amount,
                                    withholding=bool(lx.has(label, lx.WITHHOLDING, fuzzy=True))))
        return
    for bucket, phrases in (("freight", lx.FREIGHT), ("insurance", lx.INSURANCE),
                            ("excise", lx.EXCISE), ("extra", lx.EXTRA)):
        if lx.has(label, phrases) and len(label) < 45 and money_fmt:
            info.charges.append(Charge(label.strip(), amount, bucket, info.page_no))
            break
    else:
        if lx.has(label, lx.DISCOUNT) and len(label) < 60:
            info.header_discount = abs(amount)


def _tax_name(label: str) -> str:
    label = re.sub(r"\(?\d{1,2}(?:[.,]\d+)?\s?%\)?", "", label)
    label = re.sub(r"\(\s*[ivx]+\s*\)", "", label, flags=re.I)
    return re.sub(r"\s+", " ", label).strip(" :()") or "VAT"
