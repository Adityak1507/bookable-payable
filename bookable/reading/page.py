"""PageInfo: the faithful transcription of one page."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..ir import Charge, Line, Party, TaxRow
from ..numbers import money


@dataclass
class PageInfo:
    page_no: int
    doc_type: str = "OTHER"
    title: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    due_date: str = ""
    po_number: str = ""
    payment_terms_text: str = ""
    currency: str = ""
    supplier: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    vat_ids: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    tax_rows: list[TaxRow] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    header_discount: Decimal | None = None
    subtotal: Decimal | None = None
    total_tax: Decimal | None = None
    gross_candidates: list[tuple[int, Decimal, str]] = field(default_factory=list)   # (priority, value, label)
    amounts: list[Decimal] = field(default_factory=list)   # every money amount outside the item table
    intermediate_totals: list[Decimal] = field(default_factory=list)   # cascade steps, not what is owed
    prices_include_tax: bool | None = None
    consistency: float = 0.0          # share of item rows whose arithmetic closes
    text: str = ""
    source: str = ""
