"""Locale-aware money parsing and dot-decimal formatting.

The ERP only understands dot-decimal strings ("1234.56"); documents print "1.234,56",
"1 234,56", "GH₵ 5400.00", "(12.00)", "12.00-". Everything numeric passes through here
exactly once, so `erp.py` never sees a locale-formatted value (which it would read as 0).
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

CENT = Decimal("0.01")
_MINUS = "-−‒–—"
_KEEP = re.compile(r"[^0-9.,'\s()" + re.escape(_MINUS) + r"]")


def detect_decimal_sep(raws: Iterable[str]) -> str | None:
    """Infer a document's decimal separator from the numbers printed on it.

    A separator followed by exactly 1-2 trailing digits at the end of a number is
    decimal evidence; both separators in one number make the last one decimal."""
    votes: Counter[str] = Counter()
    for raw in raws:
        s = re.sub(r"[^0-9.,]", "", str(raw))
        if "." in s and "," in s:
            votes["." if s.rfind(".") > s.rfind(",") else ","] += 2
            continue
        m = re.search(r"([.,])(\d{1,2})$", s)
        if m:
            votes[m.group(1)] += 1
    if not votes:
        return None
    (best, n), *rest = votes.most_common()
    if rest and rest[0][1] == n:
        return None
    return best


def parse_amount(raw, decimal_sep: str | None = None) -> Decimal | None:
    """Parse a printed number into a Decimal, or None when it is not a number."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = str(raw).strip()
    if not s:
        return None
    s = _KEEP.sub("", s).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = s.replace("(", "").replace(")", "").strip()
    if s and s[0] in _MINUS:
        neg, s = True, s[1:]
    elif s and s[-1] in _MINUS:                       # SAP style "12.00-"
        neg, s = True, s[:-1]
    s = re.sub(r"[\s']", "", s)
    if not s or not re.search(r"\d", s):
        return None

    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
    elif dots + commas == 0:
        dec = None
    else:
        sep = "." if dots else ","
        tail = s.rsplit(sep, 1)[1]
        if s.count(sep) > 1:
            dec = None                                 # "1.234.567" -> grouping only
        elif decimal_sep is not None:
            dec = sep if sep == decimal_sep else None
        else:
            dec = sep if len(tail) != 3 else None      # "1,200" ambiguous: grouping
    if dec:
        grp = "," if dec == "." else "."
        s = s.replace(grp, "").replace(dec, ".")
    else:
        s = s.replace(".", "").replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def money(x) -> Decimal:
    """Round to cents, half-up — the same rule as the ERP's round2."""
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(d: Decimal | None) -> str:
    """Dot-decimal string for the autodraft; '' for unknown."""
    if d is None:
        return ""
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_money(d: Decimal | None) -> str:
    return "" if d is None else format(money(d), "f")


def near(a: Decimal | None, b: Decimal | None, tol: Decimal = Decimal("0.005")) -> bool:
    return a is not None and b is not None and abs(Decimal(a) - Decimal(b)) <= tol
