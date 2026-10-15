"""Header fields and parties: labelled values (number, dates, PO, terms), currency, tax ids, supplier/buyer."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field

from .. import lexicon as lx
from ..ir import Party
from ..ocr.layout import TextLine
from .cells import Cell, cells_of, _is_num
from ..rules import rule


_TAX_ID = re.compile(
    r"(?i)\b(?:VAT(?:\s*(?:ID|No\.?|Number|Reg(?:istration)?\.?(?:\s*No\.?)?|Nr\.?))?|VATNo|USt-?Id(?:Nr)?\.?|UID|"
    r"KMKR(?:\s*nr\.?)?|KM\s*reg\.?\s*nr\.?|NIF|NIPC|Contribuinte(?:\s*N\.?[oº]?)?|TIN|ABN|GST(?:\s*(?:No|Reg)\.?)?|"
    r"SST\s*Registration\s*No\.?|BTW(?:-nr)?|CIF|P\.?\s?IVA|Partita\s+IVA|CVR|Org\.?\s*nr|Tax\s*(?:ID|No\.?|Reg\.?\s*No\.?))"
    r"[\s.:#ºo-]{0,6}((?:[A-Z]{1,3}[\s-]?)?\d[\d\s.\-]{5,16}\d[A-Z]?\d{0,2})(?!\d)")


_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


_LEGAL = re.compile(r"\b(GmbH|AG|Ltd\.?|Limited|PLC|Inc\.?|LLC|O[UÜ]|AS|S\.?A\.?|Lda\.?|Unipessoal|Sdn\.? Bhd\.?|"
                    r"\(Pty\)|Pty|B\.?V\.?|N\.?V\.?|S\.?r\.?l\.?|S\.?p\.?A\.?|Oy|AB|ApS|Corp\.?|KG|SARL|SAS|CC)\b")


_BUYER_LABELS = ["bill to", "billed to", "invoice to", "invoiced to", "sold to", "customer", "client", "buyer",
                 "prepared for", "maksja", "arve maksja", "payer", "payer of invoice", "rechnungsempfanger",
                 "rechnungsadresse", "kunde", "cliente", "destinatario", "adquirente", "kupujacy", "asiakas",
                 "department accounts", "to"]


_SUPPLIER_LABELS = ["vendor", "supplier", "seller", "from", "issued by", "lieferant", "fornecedor", "proveedor",
                    "tarnija", "muuja", "vendor details", "name"]


@rule("fields.label_value", "A field value is right of or below its label; a label followed only by words is another label.")
def _after_label(lines: list[TextLine], labels: list[str], dec, validate, max_lines=None):
    """Value printed right of a label (same line) or directly below it. Specific labels are tried
    over the whole page before short generic ones ('Invoice No.' anywhere beats a bare 'No')."""
    specific = [l for l in labels if len(l) > 6]
    generic = [l for l in labels if len(l) <= 6]
    for group in (specific, generic):
        found = _after_label_pass(lines, group, dec, validate, max_lines, all_labels=labels) if group else ""
        if found:
            return found
    return ""


def _after_label_pass(lines: list[TextLine], labels: list[str], dec, validate, max_lines=None, all_labels=()):
    labels = sorted(labels, key=len, reverse=True)
    for i, ln in enumerate(lines[:max_lines] if max_lines else lines):
        cells = cells_of(ln)
        for ci, c in enumerate(cells):
            folded = re.sub(r"[^a-z0-9#% ]+", " ", lx.fold(c.text)).strip()
            lab = next((l for l in labels if folded.startswith(l)
                        and (len(l) > 6 or folded == l or not folded[len(l):len(l) + 1].isalpha())), None)
            if not lab:
                continue
            if len(lab) <= 6 and folded != lab and not re.match(r"^\s*[a-z]+\s*[:#.]", lx.fold(c.text)):
                if not re.search(r"\d", folded[len(lab):]):
                    continue                   # a short generic label ('No', 'Number') must stand alone
            tail = folded[len(lab):]
            same_field = any(tail.strip().startswith(l) for l in (all_labels or labels))  # 'Arve nr. Invoice No.'
            if re.fullmatch(r"\s*[a-z]{2,}[a-z ]*", tail) and ":" not in c.text and not same_field:
                continue                       # "Invoice Date" is another label, not "Invoice" + value
            if same_field:
                lab = folded                   # the whole bilingual label; the value follows it
            rest = re.sub(r"^[\s:.#\-–nº°o]*", "", lx.fold(c.text)[len(lab):]) if len(folded) > len(lab) else ""
            cands = [c.text[len(c.text) - len(rest):].strip()] if rest else []
            cands += [x.text for x in cells[ci + 1:ci + 3]]
            below = _cell_below(lines, i, c)
            if below:
                cands.append(below)
            for v in cands:
                v = v.strip(" :#")
                ok = validate(v)
                if ok:
                    return ok
    return ""


def _cell_below(lines, i, c: Cell) -> str:
    for ln in lines[i + 1:i + 3]:
        for d in cells_of(ln):
            if d.x0 < c.x1 and d.x1 > c.x0:
                return d.text
    return ""


def _valid_id(v: str) -> str:
    v = v.strip().split("  ")[0].strip()
    tok = v.split()[0] if v.split() else ""
    if len(v.split()) > 1 and re.search(r"\d", v.replace(" ", "")) and re.fullmatch(r"[\d ]{4,24}", v):
        tok = v.replace(" ", "")
    if 2 <= len(tok) <= 30 and re.search(r"\d", tok) and not lx.parse_date(tok) and re.fullmatch(r"[\w./-]+", tok):
        return tok
    return ""


def _valid_date(v: str) -> str:
    return lx.parse_date(v)


@rule("fields.name_not_a_field", "A party name is not mostly digits and is not another field's label.")
def _valid_name(v: str) -> str:
    """A party name: not another 'Label: value' pair (except an explicit 'Name:')."""
    m = re.match(r"^\s*([A-Za-z .]{1,20}):\s*(.*)$", v)
    if m:
        if lx.fold(m.group(1)).strip() not in ("name", "company", "company name", "vendor name", "supplier name"):
            return ""
        v = m.group(2)
    v = v.strip()
    if sum(ch.isdigit() for ch in v) > 0.3 * max(len(v.replace(" ", "")), 1):
        return ""                                    # 'Vendor Number 432145' is a field, not a name
    if lx.has(v, lx.PO_NO + lx.INVOICE_NO + lx.INVOICE_DATE + lx.DUE_DATE + lx.PAY_TERMS + lx.GROSS):
        return ""                                    # another field's label ('Your Reference')
    if len(v) < 25 and lx.has(v, _BUYER_LABELS + ["ship to", "deliver to", "delivery address", "consignee"]):
        return ""                                    # 'SHIP TO' is a label, not a party
    return v if re.search(r"[A-Za-z]{3}", v) else ""


def _valid_terms(v: str) -> str:
    return v.strip() if re.search(r"[a-zA-Z]", v) or re.search(r"\d", v) else ""


def find_currency(text: str) -> str:
    codes = re.findall(r"\b([A-Z]{3})\b", text)
    counts: dict[str, int] = {}
    for c in codes:
        if c in lx.CURRENCY_CODES:
            counts[c] = counts.get(c, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    # amounts written 'R129,514.32' (rand) — only when the page uses that form repeatedly
    if len(re.findall(r"(?<![A-Za-z$])R\s?\d{1,3}(?:[ ,]\d{3})*\.\d{2}\b", text)) >= 2:
        return "ZAR"
    low = text.lower()
    for sym, code in sorted(lx.CURRENCY_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        if (sym.isalpha() and re.search(rf"\b{re.escape(sym)}\b", low)) or (not sym.isalpha() and sym in low):
            return code
    return ""


@rule("fields.prominent_letterhead", "With no company name in the letterhead, the most prominent text at the very top of the page is the issuer; shipment parties (SHPR, CNEE) are not.")
def _prominent_top(lines: list[TextLine]) -> str:
    """Largest-type words in the top tenth of the page that are not a title, a label or a number."""
    if not lines:
        return ""
    top, span = lines[0].y0, max(l.y1 for l in lines) - lines[0].y0
    heights = sorted(l.height for l in lines)
    typical = heights[len(heights) // 2]
    for ln in sorted((l for l in lines if l.y0 - top <= 0.1 * span), key=lambda l: -l.height):
        if ln.height < 1.4 * typical:
            break
        for c in cells_of(ln):
            t = c.text.strip()
            words = re.findall(r"[a-z]{4,}", lx.fold(t))
            title_like = any(SequenceMatcher(None, w, p).ratio() >= 0.8 for w in words
                             for _, ph in lx.DOC_TYPES for p in ph if " " not in p)   # 'INNVOICE'
            if (re.search(r"[A-Za-z]{3}", t) and not re.search(r"\d", t) and ":" not in t
                    and not title_like and len(t) <= 40
                    and not lx.has(t, ["original", "copy", "duplicate", "page", "tax", "kopie", "copia"])):
                return t
    return ""


_CUSTOMER_ID = ["your vat", "your ust", "customer vat", "client vat", "buyer vat", "kunden ust", "ihre ust",
                "ust idnr des leistungsempfangers", "vat no of customer", "kliendi km", "nif do cliente", "v contribuinte",
                "your tax", "customer tax", "recipient vat"]


@rule("fields.customer_tax_id", "A tax id labelled as the customer's, or printed beside a reverse-charge note, is the buyer's.")
def _is_buyer_id(lines: list[TextLine], li: int, cell_text: str) -> bool:
    """Reverse-charge invoices must print the customer's VAT number; a 'Your VAT no.' label says
    the same. Such an id is the buyer's, never the supplier's."""
    near_text = " ".join(lines[j].text for j in range(max(0, li - 1), min(len(lines), li + 2)))
    return bool(lx.has(cell_text, _CUSTOMER_ID) or lx.has(near_text, ["reverse charge", "steuerschuldnerschaft",
                "autoliquidation", "autoliquidacion", "omvendt betalingspligt", "povinnost prenesena"]))


@rule("fields.letterhead_supplier", "The issuer's name heads the page: the first company name in the top quarter.")
def _letterhead(lines: list[TextLine], cands: list[str]) -> str:
    """The issuer's name heads the page: the first company name in the top quarter."""
    if not lines:
        return ""
    page_top = lines[0].y0
    span = max(l.y1 for l in lines) - page_top
    for ln in lines:
        if ln.y0 - page_top > 0.25 * span:
            break
        for c in cells_of(ln):
            name = re.split(r"\b(?:VAT|Reg|Tel|Phone|IBAN|E-?mail)\b", c.text.split("|")[0], flags=re.I)[0].strip(" ,:")
            if name in cands:
                return name
    return ""


@rule("fields.labelled_tax_ids", "A tax id is read only where the page labels it as one, inside its own cell.")
@rule("fields.supplier_id_nearest_name", "The supplier's tax id is the one printed nearest the supplier's name.")
def find_parties(lines: list[TextLine], text: str) -> tuple[Party, Party, list[str], list[str]]:
    vat_ids, id_line = [], {}
    buyer_ids = set()
    for li, ln in enumerate(lines):
        for c in cells_of(ln):                         # an id stays inside its own cell
            for m in _TAX_ID.finditer(c.text):
                v = re.sub(r"[\s.-]", "", m.group(1)).upper()
                if sum(ch.isdigit() for ch in v) >= 7 and v not in vat_ids:
                    vat_ids.append(v)
                    id_line[v] = li
                    if _is_buyer_id(lines, li, c.text):
                        buyer_ids.add(v)
    ibans = [re.sub(r"\s", "", m.group(0)) for m in _IBAN.finditer(text.upper()) if len(re.sub(r"\s", "", m.group(0))) >= 15]
    emails = _EMAIL.findall(text)

    buyer = Party()
    for i, ln in enumerate(lines):
        cells = cells_of(ln)
        for c in cells:
            f = re.sub(r"[^a-z ]+", " ", lx.fold(c.text)).strip()
            raw = lx.fold(c.text)
            # a label is the whole cell ("Bill To:") or ends in a colon ("Bill to: ACME")
            lab = next((l for l in sorted(_BUYER_LABELS, key=len, reverse=True)
                        if f == l or re.match(rf"^{re.escape(l)}\s*:", re.sub(r"[^a-z: ]+", " ", raw).strip())), None)
            if lab and not buyer.name:
                rest = c.text[len(lab):].strip(" :") if len(f) > len(lab) + 1 else ""
                right = cells[cells.index(c) + 1].text if cells.index(c) + 1 < len(cells) else ""
                if right and (":" in right or lx.has(right, lx.INVOICE_DATE + lx.INVOICE_NO + lx.DUE_DATE)
                              or _is_num(right)):
                    right = ""                          # the next cell is another field, not the name
                name = rest or right or _cell_below(lines, i, c)
                if name and re.search(r"[A-Za-z]", name):
                    buyer.name = name.strip()
                    addr = []
                    for ln2 in lines[i + 1:i + 5]:
                        for d in cells_of(ln2):
                            if d.x0 < c.x1 + 150 and d.x1 > c.x0 - 10 and d.text.strip() != buyer.name:
                                addr.append(d.text.strip())
                    buyer.address = ", ".join(addr[:3])
    supplier = Party()
    legal = [c.text.split("|")[0].strip() for ln in lines for c in cells_of(ln) if _LEGAL.search(c.text)]
    legal = [re.split(r"\b(?:VAT|Reg|Tel|Phone|IBAN|E-?mail)\b", t, flags=re.I)[0].strip(" ,:") for t in legal]
    # a name carrying a shipment role ('SHPR:', 'CNEE:', 'Shipper:') is a party of the shipment,
    # not the issuer of the invoice; other short 'XXX:' prefixes are just stripped
    legal = [t for t in legal if not re.match(r"^\s*(?:SHPR|CNEE|NTFY|shipper|consignee|notify)\b", t, re.I)]
    legal = [re.sub(r"^[A-Z]{2,6}:\s*", "", t) for t in legal]
    # a company name has a real word besides its legal form ('10 KG' is ten kilograms)
    cands = [t for t in legal if t and re.search(r"[A-Za-z]{3}", _LEGAL.sub("", t))
             and not re.match(r"^\s*\d", t)
             and (not buyer.name or lx.fold(buyer.name)[:12] not in lx.fold(t))]
    labelled = _after_label(lines, _SUPPLIER_LABELS, None, _valid_name)
    supplier.name = labelled or _letterhead(lines, cands) or _prominent_top(lines) or (cands[-1] if cands else "")
    # the supplier's id is the one printed nearest its name (buyers' ids are often printed too)
    name_lines = [i for i, ln in enumerate(lines) if supplier.name and supplier.name[:15] in ln.text]
    own = [v for v in vat_ids if v not in buyer_ids]            # ids the page marks as the customer's are out
    if own and name_lines:
        supplier.vat_id = min(own, key=lambda v: min(abs(id_line[v] - n) for n in name_lines))
    else:
        supplier.vat_id = own[0] if own else ""
    buyer.vat_id = next(iter(sorted(buyer_ids)), "")
    supplier.iban = ibans[0] if ibans else ""
    supplier.email = emails[0] if emails else ""
    supplier.alternatives = [c for c in dict.fromkeys(cands) if c != supplier.name]
    return supplier, buyer, vat_ids, ibans
