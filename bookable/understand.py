"""Page understanding: PageLayout -> PageInfo (a faithful transcription of one page).

Labels propose, arithmetic disposes. Column roles in a line table are confirmed by
qty × price ≈ total across rows; a tax summary row is recognised by rate × base ≈ amount;
the gross is the labelled total that the page's own components add up to. Nothing here
decides how the ERP should book — that is solve.py's job.
"""
from __future__ import annotations

from . import lexicon as lx
from .numbers import detect_decimal_sep
from .ocr.layout import PageLayout
from .reading.cells import (  # noqa: F401  — re-exported for callers and tests
    HUNDRED, Cell, _NUM_CELL, _CUR_PREFIX, cells_of, _split_number_label, _split_numbers, _thousands_join, _strip_currency, _is_num, num, _QTY_UNIT, _ORD_DEL, _QTY_TIMES, row_numbers, is_code, _is_currency, page_from_text)
from .reading.page import (  # noqa: F401  — re-exported for callers and tests
    PageInfo)
from .reading.classify import (  # noqa: F401  — re-exported for callers and tests
    _FIELD_MARK, _title_phrase, classify)
from .reading.fields import (  # noqa: F401  — re-exported for callers and tests
    _TAX_ID, _IBAN, _EMAIL, _LEGAL, _BUYER_LABELS, _SUPPLIER_LABELS, _after_label, _cell_below, _valid_id, _valid_date, _valid_name, _valid_terms, find_currency, _letterhead, find_parties)
from .reading.items import (  # noqa: F401  — re-exported for callers and tests
    _ROLE_LISTS, _subcells, _header_roles, _header_roles_on, _is_header, _row_from_numbers, _assign_rate_column, _closes, _header_at, extract_items, _is_tax_line, _closing_rows, _summing_rows)
from .reading.summary import (  # noqa: F401  — re-exported for callers and tests
    _summary_label, _GROSS_PRIORITY, _label_left, _label_above, _label_value_segments, summary_entries, extract_summary, _summary_entry, _tax_name)


def understand(page: PageLayout, dec: str | None = None) -> PageInfo:
    lines = page.lines()
    text = "\n".join(l.text for l in lines)
    dec = dec or detect_decimal_sep(w.text for w in page.words)
    info = PageInfo(page.page_no, text=text, source=page.source)
    info.doc_type, info.title = classify(lines, page.height)
    info.invoice_number = _after_label(lines, lx.INVOICE_NO, dec, _valid_id)
    dmy = lx.date_order(text)
    valid_date = lambda v: lx.parse_date(v, prefer_dmy=dmy)  # noqa: E731
    info.invoice_date = _after_label(lines, lx.INVOICE_DATE, dec, valid_date)
    info.due_date = _after_label(lines, lx.DUE_DATE, dec, valid_date)
    info.po_number = _after_label(lines, lx.PO_NO, dec, _valid_id)
    info.payment_terms_text = _after_label(lines, lx.PAY_TERMS, dec, _valid_terms)
    info.currency = find_currency(text)
    info.supplier, info.buyer, info.vat_ids, info.ibans = find_parties(lines, text)
    items, cons, a, b = extract_items(lines, dec)
    info.lines, info.consistency = items, cons
    extract_summary(info, lines, dec, range(a, b + 1) if a >= 0 else range(0))
    if not info.lines:
        found = _summing_rows(lines, dec, info)
        if found:
            rows, a, b = found
            fresh = PageInfo(info.page_no)                    # re-read the summary without those rows
            extract_summary(fresh, lines, dec, range(a, b + 1))
            for f in ("tax_rows", "charges", "header_discount", "subtotal", "total_tax",
                      "gross_candidates", "prices_include_tax", "amounts"):
                setattr(info, f, getattr(fresh, f))
            info.lines, info.consistency = rows, 1.0
    for li in info.lines:
        li.page = page.page_no
    return info
