"""The intermediate record: a faithful transcription of ONE payable document.

This layer copies the page — every number as printed (already dot-decimal), with where it
came from. It makes no ERP decisions. Translation to the ERP's model happens in `solve.py`,
which is the only place that decides what the ERP should be told.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

D = Decimal | None


@dataclass
class Party:
    name: str = ""
    address: str = ""
    vat_id: str = ""
    iban: str = ""
    email: str = ""
    country: str = ""
    extra_ids: list[str] = field(default_factory=list)   # every VAT id / IBAN printed on the page
    alternatives: list[str] = field(default_factory=list)   # other company names printed on the page


@dataclass
class Line:
    description: str = ""
    quantity: D = None
    unit_price: D = None
    total: D = None                  # line extension as printed
    discount_pct: D = None
    discount_amount: D = None
    tax_rate: D = None               # rate printed on the line
    tax_amount: D = None             # tax amount printed on the line
    tax_name: str = ""
    uom: str = ""
    kind: str = ""                   # GOODS | SERVICE | FREIGHT (hint)
    price_basis: str = ""            # "gross" when the column says prices include tax
    page: int = 0


@dataclass
class TaxRow:
    """A tax stated in a summary / header block (not on a line)."""
    name: str = ""
    rate: D = None
    base: D = None                   # printed base, if any
    amount: D = None
    withholding: bool = False        # the document says it is deducted
    page: int = 0


@dataclass
class Charge:
    label: str = ""
    amount: D = None
    bucket: str = "extra"            # freight | insurance | extra | excise
    page: int = 0


@dataclass
class DocIR:
    doc_type: str = "INVOICE"        # INVOICE | CREDIT_NOTE | ...
    title: str = ""
    invoice_number: str = ""
    invoice_date: str = ""           # ISO
    due_date: str = ""
    currency: str = ""
    supplier: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    payment_terms_text: str = ""
    po_number: str = ""

    lines: list[Line] = field(default_factory=list)
    tax_rows: list[TaxRow] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    header_discount: D = None

    subtotal: D = None
    total_tax: D = None
    gross: D = None                  # what the document says is owed
    gross_inferred: bool = False     # no total printed: gross is the sum of printed lines
    prices_include_tax: bool | None = None   # printed hint ("incl. VAT"), None if silent
    pages: list[int] = field(default_factory=list)
    source_file: str = ""
    text: str = ""                   # the document's page text, for evidence that is not a field
