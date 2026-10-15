"""Multilingual accounting vocabulary and date parsing.

This is domain vocabulary — what "total", "VAT", "credit note" are called across languages —
not knowledge about particular suppliers or documents. Labels only PROPOSE a reading;
the document's arithmetic decides which proposal is right (see understand.py / solve.py).
All phrases are matched after accent-stripping and lower-casing.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher


_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L",
                           "đ": "d", "Đ": "D", "þ": "th", "ð": "d", "œ": "oe", "ı": "i"})


def fold(s: str) -> str:
    s = (s or "").translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def has(text: str, phrases, fuzzy: bool = False) -> str | None:
    """First phrase found as a whole word/phrase in folded text. With fuzzy=True, a phrase of
    8+ characters also matches an OCR-damaged spelling ('(OTAL PAYMEN)', 'WITHOLDING TA')."""
    t = " " + re.sub(r"[^a-z0-9%]+", " ", fold(text)) + " "
    for p in phrases:
        if f" {p} " in t:
            return p
    if fuzzy:
        # OCR confuses I/l with 1 and O with 0 inside labels ('1 alt DKK' is 'I alt DKK')
        t = re.sub(r"(?<= )1(?= [a-z])", "i", t)
        t = re.sub(r"(?<=[a-z])0|0(?=[a-z])", "o", t)
        for p in phrases:
            if f" {p} " in t:
                return p
        words = t.split()
        for p in phrases:
            n = len(p.split())
            if len(p) < 8:
                continue
            for k in range(len(words) - n + 1):
                if SequenceMatcher(None, " ".join(words[k:k + n]), p).ratio() >= 0.86:
                    return p
    return None


# ── document types (title phrases). Order = precedence when several appear. ──
DOC_TYPES: list[tuple[str, list[str]]] = [
    ("PROFORMA", ["proforma", "pro forma", "proforma invoice", "pro forma invoice", "fatura proforma",
                  "factura proforma", "proformarechnung", "proforma arve"]),
    ("CREDIT_NOTE", ["credit note", "credit memo", "credit invoice", "gutschrift", "rechnungskorrektur",
                     "nota de credito", "nota credito", "avoir", "kreeditarve", "kreeditteatis", "creditnota",
                     "kreditnota", "kreditfaktura", "hyvityslasku", "storno"]),
    ("DUNNING", ["mahnung", "zahlungserinnerung", "payment reminder", "reminder", "dunning", "meeldetuletus",
                 "rappel", "overdue notice", "past due notice", "aviso de cobranca", "betalingspaamindelse",
                 "final demand"]),
    ("DELIVERY_NOTE", ["delivery note", "lieferschein", "packing list", "packing slip", "delivery order",
                       "despatch note", "dispatch note", "guia de remessa", "guia de transporte", "saateleht",
                       "albaran", "bon de livraison", "folgeseite lieferschein", "proof of delivery"]),
    ("CUSTOMS_DECLARATION", ["customs declaration", "declaracao aduaneira", "estancia aduaneira",
                             "single administrative document", "gumruk beyannamesi", "beyanname",
                             "import declaration", "customs entry", "entry summary", "zollanmeldung",
                             "tolldeklaration", "customs permit", "import permit"]),
    ("WAYBILL", ["air waybill", "airwaybill", "waybill", "bill of lading", "consignment note", "cmr",
                 "house air waybill", "master air waybill"]),
    ("REMITTANCE", ["payment advice", "remittance advice", "remittance slip", "remittance", "payment slip",
                    "zahlschein", "uberweisungstrager", "maksekorraldus", "talao de pagamento"]),
    ("STATEMENT", ["statement of account", "account statement", "kontoauszug", "saldoteatis",
                   "extrato de conta"]),
    ("ORDER", ["purchase order", "order confirmation", "quotation", "quote", "estimate", "cost estimate",
               "kostenvoranschlag", "orcamento", "presupuesto", "devis", "angebot", "auftragsbestatigung",
               "orcamento", "pakkumine", "sales order"]),
    ("FORM", ["application form", "registration form", "vendor form", "supplier form", "questionnaire",
              "request form", "form w 9", "formulario", "antrag"]),
    ("INVOICE", ["tax invoice", "invoice", "rechnung", "arve", "fatura", "factura", "facture", "fattura",
                 "faktura", "faktuur", "factuur", "lasku", "afregning", "rachunek", "szamla", "racun",
                 "fatura recibo", "fatura simplificada", "commercial invoice", "billing statement",
                 "nota fiscal", "invoice no", "invoice number"]),
]
PAYABLE_TYPES = {"INVOICE", "CREDIT_NOTE"}

# ── header labels ──
INVOICE_NO = ["invoice no", "invoice number", "invoice nr", "invoice num", "invoice id", "invoice #", "inv no",
              "tax invoice no", "credit note no", "credit note number", "credit memo no", "document no",
              "document number", "rechnungsnummer", "rechnung nr", "rechnungs nr", "belegnummer", "gutschrift nr",
              "arve nr", "arve number", "fatura n", "fatura no", "fatura nr", "factura n", "factura no",
              "numero da fatura", "numero de factura", "n fatura", "faktura nr", "fakturanummer", "faktura no",
              "bill no", "bill number", "no facture", "numero de facture", "laskun numero", "nr faktury",
              "afregning nr", "afregningsnr", "invoice", "arve", "rechnung", "fatura", "factura",
              "number", "no", "nr"]
INVOICE_DATE = ["invoice date", "date of invoice", "date of issue", "issue date", "issued", "rechnungsdatum",
                "datum", "kuupaev", "data de emissao", "data da fatura", "data", "fecha", "fecha de emision",
                "fakturadato", "fakturadatum", "date facture", "date", "dato", "paivays", "tax point"]
DUE_DATE = ["due date", "payment due", "pay by", "faellig", "fallig", "falligkeitsdatum", "zahlbar bis",
            "maksetahtaeg", "tasumistahtaeg", "data de vencimento", "vencimento", "vencimiento", "forfaldsdato",
            "betalingsdato", "date d echeance", "echeance", "erapaiva", "termin platnosci", "due"]
PO_NO = ["your p o number", "your po number", "p o number", "po number", "po no", "po #", "purchase order",
         "purchase order no", "order reference", "bestellnummer", "ihre bestellung", "bestellung",
         "nota de encomenda", "encomenda", "tellimus", "pedido", "your order", "your reference", "your ref",
         "customer reference", "cust ref", "po"]
PAY_TERMS = ["payment terms", "terms of payment", "terms", "zahlungsbedingungen", "zahlungsziel",
             "tasumistingimus", "maksetingimused", "condicoes de pagamento", "condiciones de pago",
             "betalingsbetingelser", "conditions de paiement", "maksuehdot"]

# ── summary-block labels ──
GROSS = ["sub total c iva", "subtotal c iva", "amount due", "total due", "balance due", "summa koos kaibemaksuga", "kokku koos km", "tasumata",
         "tasumisele kuuluv summa", "arve summa", "net current reimbursement", "total reimbursement",
         "net reimbursement", "amount to be reimbursed", "total payment", "payment total", "net payment",
         "total payable", "total amount due", "amount payable", "to pay", "tasuda",
         "tasumisele kuulub", "zu zahlen", "zahlbetrag", "rechnungsbetrag", "gesamtbetrag", "endbetrag",
         "total a pagar", "valor a pagar", "total da fatura", "importe total", "total factura", "total ttc",
         "net a payer", "at betale", "total til betaling", "grand total", "invoice total", "total amount",
         "total tax inclusive value", "total incl vat", "total incl", "total including vat", "total inc vat",
         "kokku", "kokku tasuda", "summe", "gesamt", "total", "outstanding balance", "total eur", "total usd",
         "total gbp", "balance", "gesamtsumme", "rechnungssumme", "bruttobetrag", "brutto", "kokku summa", "summa kokku", "total geral", "totaal", "yhteensa", "i alt", "total a liquidar"]
SUBTOTAL = ["subtotal", "sub total", "net amount", "net total", "total net", "total excl vat", "total excl",
            "amount excl vat", "nettobetrag", "netto", "summe netto", "neto", "neto summa", "summa km ta",
            "base de incidencia", "base tributavel", "base imponible", "tax exclusive value", "total ht",
         "carried forward", "c f", "overfort", "uebertrag", "ubertrag", "a transportar", "transporte a",
         "kanne edasi",
            "total before tax", "taxable amount", "liquido", "valor liquido", "zwischensumme", "vahesumma", "summa ilma kaibemaksuta", "nettosumme", "summe netto", "netto summa", "kokku ilma km", "total sem iva", "subtotaal"]
TOTAL_TAX = ["total tax", "total vat", "vat total", "iva total", "tax amount", "vat amount", "total iva",
             "mwst gesamt", "umsatzsteuer gesamt", "km kokku", "kaibemaks kokku", "kaibemaks", "moms i alt",
             "total tva", "tax total"]
TAX_WORDS = ["vat", "iva", "tax", "mwst", "ust", "km", "kaibemaks", "gst", "sst", "hst", "pst", "nhil",
             "getfund", "covid", "levy", "cst", "moms", "tva", "btw", "mva", "dph", "alv", "fpa", "kdv",
             "umsatzsteuer", "mehrwertsteuer", "service tax", "sales tax", "use tax", "withholding", "wht",
             "retencao", "retencion", "imposto", "impuesto"]
TAX_BASE = ["alus", "km alus", "base", "tax base", "vat base", "taxable", "basis", "incidencia", "base de incidencia",
            "bemessungsgrundlage", "grundlag", "momsgrundlag", "base imponible", "base tributavel"]
WITHHOLDING = ["withholding", "wht", "withholding tax", "withholding ta", "retencao", "retencion", "retention", "quellensteuer",
               "less wht", "irs retencao"]
FREIGHT = ["freight", "shipping", "delivery", "delivery cost", "delivery charge", "transport", "carriage",
           "postage", "fracht", "versand", "versandkosten", "porte", "portes", "frete", "transporte",
           "envio", "fragt", "kohaletoimetamine", "saatmine", "postituskulut"]
INSURANCE = ["insurance", "versicherung", "seguro", "kindlustus", "forsikring", "assurance"]
EXCISE = ["excise", "excise duty", "iec", "iaba", "verbrauchsteuer", "aktsiis", "duty"]
EXTRA = ["fuel surcharge", "surcharge", "handling", "handling fee", "service fee", "service charge",
         "agency fee", "management fee", "admin fee", "administration fee", "deposit", "deposito", "pfand", "rounding",
         "arredondamento", "rundung", "umartamine", "packaging", "verpackung", "encargos", "caucionamento",
         "other charges", "misc", "toll"]
# amounts deducted AFTER tax: already paid, or credited against this invoice
DEDUCTION = ["less amount credited", "amount credited", "less credit", "credit applied", "credits applied",
             "credits adjustments", "less payments", "less payment", "payments received", "amount paid", "paid",
             "deposit paid", "less deposit", "prepayment", "prepaid", "advance payment", "less advance",
             "abzuglich anzahlung", "anzahlung", "bereits bezahlt", "geleistete anzahlung", "ettemaks",
             "tasutud", "ja pago", "adiantamento", "pagado", "anticipo", "acompte", "deja paye"]
DISCOUNT = ["discount", "rabatt", "desconto", "descuento", "allahindlus", "remise", "skonto", "rabat",
            "desc prom", "desc com"]
INCL_HINT = ["incl", "inkl", "incl vat", "inkl mwst", "incl tax", "including vat", "vat inclusive", "inc vat", "c iva",
             "com iva", "iva incluido", "ttc", "km ga", "sis km", "incl moms", "inkl moms", "tax inclusive",
             "incl gst", "gst inclusive"]

EXCL_HINT = ["excl", "excl vat", "ex vat", "ex gst", "exkl", "ohne mwst", "netto", "s iva", "sem iva", "sin iva",
             "ht", "net", "before tax", "ilma km"]

# ── line-table column headers ──
COL_QTY = ["qty", "quantity", "menge", "anzahl", "kogus", "quant", "qtd", "quantidade", "cantidad", "units",
           "antal", "maara", "hours", "hrs", "ordered delivered", "delivered", "qty no of users", "pcs", "stk"]
COL_PRICE = ["unit price", "price", "unit cost", "rate", "preis", "einzelpreis", "stuckpreis", "hind",
             "uhiku hind", "preco unitario", "preco", "unitario", "precio", "precio unitario", "prix unitaire",
             "a pris", "enhedspris", "yksikkohinta", "cena", "unit price incl vat"]
COL_TOTAL = ["total", "amount", "cost", "charge", "charges", "net amount", "line total", "extended amount", "summa", "betrag", "gesamt",
             "valor", "importe", "montant", "belob", "belop", "yhteensa", "kokku", "neto summa", "net"]
COL_DISC = ["disc", "discount", "rabatt", "desc", "descuento", "desconto", "allahindlus", "prom", "remise"]
COL_RATE = ["vat", "vat %", "tax", "tax %", "km", "km %", "iva", "iva %", "mwst", "ust", "%", "gst", "moms"]
COL_DESC = ["description", "item", "items", "product", "designation", "designacao", "nimetus", "bezeichnung",
            "beschreibung", "descricao", "descripcion", "artikel", "leistung", "text", "notes", "service",
            "article", "tekst", "particulars", "details"]
COL_UOM = ["unit", "uom", "un", "uhik", "einheit", "unidade", "unidad", "enhed"]

# the word an issuer uses for its own tax points to its country
TAX_WORD_COUNTRIES = {"mwst": {"DE", "AT", "CH"}, "ust": {"DE", "AT"}, "umsatzsteuer": {"DE", "AT"},
                      "mehrwertsteuer": {"DE", "AT", "CH"}, "iva": {"PT", "ES", "IT"}, "km": {"EE"},
                      "kaibemaks": {"EE"}, "moms": {"DK", "SE"}, "mva": {"NO"}, "alv": {"FI"}, "tva": {"FR", "BE"},
                      "btw": {"NL", "BE"}, "nhil": {"GH"}, "getfund": {"GH"}, "sst": {"MY"},
                      "dph": {"CZ", "SK"}, "kdv": {"TR"}, "fpa": {"GR"}}

CURRENCY_CODES = {"EUR", "USD", "GBP", "ZAR", "GHS", "KES", "MYR", "SGD", "THB", "CHF", "DKK", "SEK", "NOK",
                  "PLN", "RON", "VND", "CAD", "AUD", "TRY", "CNY", "JPY", "INR", "NGN", "AED", "HUF", "CZK",
                  "NZD", "HKD", "PHP", "IDR", "BRL", "MXN", "EGP", "UGX", "TZS", "XOF", "MAD"}
CURRENCY_SYMBOLS = {"baht": "THB", "euro": "EUR", "euros": "EUR", "rand": "ZAR", "ringgit": "MYR",
                    "cedi": "GHS", "cedis": "GHS", "shillings": "KES", "€": "EUR", "£": "GBP", "₵": "GHS", "gh₵": "GHS", "ghc": "GHS", "ghs": "GHS",
                    "rm": "MYR", "ksh": "KES", "kshs": "KES", "chf": "CHF", "s$": "SGD", "฿": "THB",
                    "zł": "PLN", "₺": "TRY", "₹": "INR", "₫": "VND"}

MONTHS = {m: i + 1 for i, names in enumerate([
    ["jan", "january", "januar", "janeiro", "enero", "jaanuar", "janvier", "tammikuu"],
    ["feb", "february", "februar", "fevereiro", "febrero", "veebruar", "fevrier", "helmikuu"],
    ["mar", "march", "marz", "maerz", "marco", "marzo", "marts", "mars", "maaliskuu"],
    ["apr", "april", "abril", "aprill", "avril", "huhtikuu"],
    ["may", "mai", "maio", "mayo", "toukokuu"],
    ["jun", "june", "juni", "junho", "junio", "juuni", "juin", "kesakuu"],
    ["jul", "july", "juli", "julho", "julio", "juuli", "juillet", "heinakuu"],
    ["aug", "august", "agosto", "aout", "elokuu"],
    ["sep", "sept", "september", "setembro", "septiembre", "septembre", "syyskuu"],
    ["oct", "october", "oktober", "outubro", "octubre", "oktoober", "octobre", "lokakuu"],
    ["nov", "november", "novembro", "noviembre", "novembre", "marraskuu"],
    ["dec", "december", "dezember", "dezembro", "diciembre", "detsember", "decembre", "joulukuu"],
]) for m in names}


def parse_date(text: str, prefer_dmy: bool = True) -> str:
    """ISO date from the first date-looking substring, or ''."""
    t = fold(text)
    m = re.search(r"\b(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\b", t)
    if m:
        return _iso(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})\s*[-/. ]\s*([a-z]{3,10})\.?\s*[-/. ,]\s*(\d{2,4})\b", t)
    if m and m[2] in MONTHS:
        return _iso(_year(m[3]), MONTHS[m[2]], int(m[1]))
    m = re.search(r"\b([a-z]{3,10})\.?\s+(\d{1,2}),?\s+(\d{4})\b", t)
    if m and m[1] in MONTHS:
        return _iso(int(m[3]), MONTHS[m[1]], int(m[2]))
    m = re.search(r"\b(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{2,4})\b", t)
    if m:
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        if a > 12:
            return _iso(y, b, a)
        if b > 12:
            return _iso(y, a, b)
        return _iso(y, b, a) if prefer_dmy else _iso(y, a, b)
    return ""


def date_order(text: str) -> bool:
    """True for day-first. Any numeric date on the page with a first part > 12 proves day-first,
    a second part > 12 proves month-first; otherwise day-first (the common convention)."""
    dmy = mdy = 0
    for a, b in re.findall(r"\b(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}\b", text):
        dmy += int(a) > 12
        mdy += int(b) > 12
    return not (mdy > dmy)


def _year(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def _iso(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""
