"""Cells and numbers: split a visual line into cells, read printed numbers (any locale, units, codes)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .. import lexicon as lx
from ..numbers import parse_amount
from ..ocr.layout import PageLayout, TextLine, Word
from ..rules import rule


HUNDRED = Decimal(100)


_NUM_CELL = re.compile(r"^[-−(]?\s*[-−]?\d[\d\s.,'’]*\)?-?\s?%?$")


_CUR_PREFIX = re.compile(r"^\s*(?:" + "|".join(sorted(map(re.escape, lx.CURRENCY_CODES | {
    "GH₵", "GHC", "RM", "KSh", "KShs", "S$", "R", "kr", "Kr"}), key=len, reverse=True)) + r"|[€£$₵฿₺₹])\.?\s?")
# A tax id is only read where the page labels it as one ('VAT No.', 'KMKR', 'NIF', 'TIN', ...).


@dataclass
class Cell:
    text: str
    x0: float
    x1: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


def cells_of(line: TextLine) -> list[Cell]:
    """Split a visual line into cells at gaps wider than ~1 character height."""
    out: list[Cell] = []
    gap = 0.9 * line.height
    for w in line.words:
        if out and w.x0 - out[-1].x1 <= gap and not (_is_num(w.text) and _is_num(out[-1].text)
                                                      and not _thousands_join(out[-1].text, w.text)):
            out[-1] = Cell(out[-1].text + " " + w.text, out[-1].x0, w.x1)
        else:
            out.append(Cell(w.text, w.x0, w.x1))
    return [part for c in out for p0 in _split_number_label(c) for part in _split_numbers(p0)]


@rule("cells.amount_then_label", "An amount boxed together with the next column's label is two cells.")
def _split_number_label(c: Cell) -> list[Cell]:
    """'1.049,60 Sub-Total c/ IVA:' — an amount and the next column's label boxed together."""
    m = re.match(r"^([-−]?\d[\d.,]*[.,]\d{2})\s+([A-Za-zÀ-ÿ].*)$", c.text.strip())
    if not m:
        return [c]
    width = (c.x1 - c.x0) / max(len(c.text.strip()), 1)
    cut = c.x0 + width * m.start(2)
    return [Cell(m.group(1), c.x0, c.x0 + width * len(m.group(1))), Cell(m.group(2), cut, c.x1)]


@rule("cells.merged_amounts", "A box holding only several amounts/rates (incl. currency prefixes) is one cell per amount.")
def _split_numbers(c: Cell) -> list[Cell]:
    """OCR sometimes boxes neighbouring columns together ('16,00 EUR 23 %', '143,09 23,0 %').
    A box made only of several amounts/rates is split into one cell per amount."""
    t = c.text.strip()
    if not re.search(r"\d[.,]\d", t):
        return [c]
    prefix = r"(?:(?<![A-Za-z])(?:R|RM|Kr|kr|KSh|GH₵|S\$)(?=\d))?"     # 'R129,514.32' (rand) etc.
    toks = list(re.finditer(prefix + r"[-−]?\d[\d.,]*(?:\s?%)?(?:\s(?:[A-Z]{3}|[€£$])(?=\s|$))?", t))
    if len(toks) < 2 or re.sub(prefix + r"[\d.,%\s€£$−-]|\b[A-Z]{3}\b", "", t):
        return [c]
    if not all(re.search(r"[.,]\d|%", m.group(0)) for m in toks):
        return [c]                               # '1 069.00' style grouping is not two numbers
    width = (c.x1 - c.x0) / max(len(t), 1)
    return [Cell(m.group(0).strip(), c.x0 + m.start() * width, c.x0 + m.end() * width) for m in toks]


@rule("cells.thousands_group", "A digit followed closely by a 3-digit group is one number (1 069.00).")
def _thousands_join(left: str, right: str) -> bool:
    """'1' + '069.00' printed with a thin space is one number (1 069.00)."""
    return bool(re.fullmatch(r"[-−]?\d{1,3}", left.strip()) and re.fullmatch(r"\d{3}(?:[.,]\d{1,3})?", right.strip()))


def _strip_currency(t: str) -> str:
    t = t.strip().rstrip("*#†‡¹²³").strip()
    lead = re.match(r"^[-−(]", t)
    body = t[1:] if lead else t
    body = _CUR_PREFIX.sub("", body, count=1) if re.search(r"\d", body) else body
    body = re.sub(r"\s?(?:" + "|".join(lx.CURRENCY_CODES) + r"|[€£$₵])$", "", body.strip())
    return (lead.group(0) if lead else "") + body.strip()


def _is_num(t: str) -> bool:
    t = _strip_currency(t)
    return bool(t) and bool(_NUM_CELL.match(t)) and any(c.isdigit() for c in t)


def num(t: str, dec) -> Decimal | None:
    t = _strip_currency(t)
    if not _is_num(t):
        return None
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", t):       # a date, not a number
        return None
    return parse_amount(t, dec)


_QTY_UNIT = re.compile(r"^([-−]?\d+(?:[.,]\d+)?)\s*([A-Za-zÀ-ÿ]{1,8}\.?(?:\(\d+\))?)$")


_ORD_DEL = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")


_QTY_TIMES = re.compile(r"^(\d{1,4})\s*[x×](?=\s|$)", re.I)


@rule("cells.quantity_forms", "A quantity may carry a unit (4 Std.), a times sign (9x) or be ordered/delivered (2/2 -> 2).")
def row_numbers(cells: list[Cell], dec) -> tuple[list[tuple[Cell, Decimal]], str]:
    """Numbers in an item row. A quantity may carry its unit ('4 Std.', '6 EA', '2 CS(6)');
    'ordered/delivered' pairs ('2/2', '1/0') contribute the delivered quantity."""
    out, uom = [], ""
    for c in cells:
        t = c.text.strip()
        v = num(t, dec)
        if v is not None and not is_code(t):
            out.append((c, v))
            continue
        m = _QTY_TIMES.match(t)                  # '9x Led Bar', '2×'
        if m:
            width = (c.x1 - c.x0) / max(len(t), 1)
            out.append((Cell(m.group(0).strip(), c.x0, c.x0 + width * len(m.group(0))), Decimal(m.group(1))))
            continue
        m = _ORD_DEL.match(t)
        if m:
            out.append((c, Decimal(m.group(2))))
            continue
        m = _QTY_UNIT.match(t)
        if m and lx.fold(m.group(2)).rstrip(".") not in {c.lower() for c in lx.CURRENCY_CODES}:
            v = parse_amount(m.group(1), dec)
            if v is not None:
                out.append((c, v))
                uom = uom or m.group(2)
    return out, uom


@rule("cells.codes_not_amounts", "Long digit runs and hyphenated references are codes, not amounts.")
def is_code(t: str) -> bool:
    """Article codes / references: long digit runs without decimals, or hyphenated ids."""
    t = t.strip()
    return bool(re.fullmatch(r"\d{5,}", t) or re.fullmatch(r"\d+[-/]\d+(?:[-/]\d+)*", t) and not re.search(r"[.,]\d{2}$", t))


def _is_currency(t: str) -> bool:
    t = t.strip().strip(":()")
    return t.upper() in lx.CURRENCY_CODES or t.lower() in lx.CURRENCY_SYMBOLS


def page_from_text(text: str, page_no: int = 1) -> PageLayout:
    """Test helper: a layout from plain text where 2+ spaces separate cells."""
    words, y = [], 0.0
    for raw in text.splitlines():
        x = 0.0
        for chunk in re.split(r"(\s{2,})", raw):
            if not chunk.strip():
                x += 6 * len(chunk) + 12
                continue
            for tok in chunk.split(" "):
                if tok:
                    words.append(Word(tok, x, y, x + 6 * len(tok), y + 10))
                x += 6 * len(tok) + 6
        y += 14
    return PageLayout(page_no, 1000, max(y, 1), words)
