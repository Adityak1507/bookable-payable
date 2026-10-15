"""Document type from the page's title — a type named inside a field label ('Invoice No.') is not a title."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import lexicon as lx
from ..ocr.layout import TextLine
from .cells import num
from ..rules import rule


_FIELD_MARK = re.compile(r"^\s*(?:no|nr|number|num|n|#|:|date|datum|ref|reference|id|kuupaev|data)\b|^\s*[:#]")


@rule("classify.title_not_field_label", "A type named in an ordinary-size field label (Quotation No.) refers to another document; set large it is the title.")
def _title_phrase(text: str, phrases, prominent: bool = False) -> str | None:
    """A document-type phrase used as a TITLE. In ordinary-sized text, 'Invoice No.', 'Quotation
    No.', 'Purchase Order:' are field labels referring to other documents; set large, 'Credit
    Note No. 123' is the document's own heading."""
    folded = " " + re.sub(r"[^a-z0-9%:#]+", " ", lx.fold(text)) + " "
    for p in phrases:
        for m in re.finditer(rf"(?<=\s){re.escape(p)}(?=[\s:#\d])", folded):   # 'Invoice20423591'
            if prominent or not _FIELD_MARK.match(folded[m.end():]):
                return p
    return None


@rule("classify.prominent_title", "The document type is the most prominent title phrase in the upper page.")
def classify(lines: list[TextLine], page_h: float) -> tuple[str, str]:
    """Document type from the most prominent title phrase in the upper part of the page."""
    best = None
    heights = sorted(l.height for l in lines) or [1.0]
    typical = heights[len(heights) // 2]
    for i, ln in enumerate(lines):
        for dtype, phrases in lx.DOC_TYPES:
            p = _title_phrase(ln.text, phrases, prominent=ln.height >= 1.25 * typical)
            if not p:
                continue
            top = ln.y0 < 0.45 * page_h
            short = len(ln.text) <= 60
            score = ln.height * (1.6 if top else 1.0) * (1.3 if short else 0.8) * (1.0 + 0.05 * len(p))
            if best is None or score > best[0] * 1.05:
                best = (score, dtype, p)
            break                      # precedence within one line: first type listed wins
    if best is None:
        return "OTHER", ""
    return best[1], best[2]
