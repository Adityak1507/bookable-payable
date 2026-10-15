"""Master-data resolution: printed values -> ERP codes, or an honest blank.

Built for masters with millions of rows: every lookup goes through a hash index
(exact keys) or an inverted token index (bounded candidate set, then scored). Nothing
scans a whole master per document. A code is only emitted on positive evidence; when the
evidence is ambiguous (two taxes at 15% and no country), the answer is "".
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

from . import lexicon as lx
from .ir import DocIR
from .numbers import parse_amount
from .rules import rule

# Legal-form and filler tokens carry no identity.
_STOP = {"gmbh", "ag", "ltd", "limited", "plc", "inc", "llc", "ou", "as", "sa", "lda", "unipessoal",
         "sdn", "bhd", "pty", "bv", "nv", "srl", "spa", "oy", "ab", "aps", "co", "corp", "company",
         "the", "and", "of", "de", "do", "da", "kg", "sarl", "sas", "kft", "sp", "zoo"}
# Words that, in many languages, name the general sales tax.
_VAT_WORDS = {"vat", "iva", "km", "kaibemaks", "mwst", "ust", "tva", "btw", "moms", "mva", "dph", "afa",
              "pvm", "ddv", "pdv", "dds", "kdv", "alv", "fpa", "mehrwertsteuer", "umsatzsteuer"}
_VAT_TYPES = {"VAT", "IVA", "MOMS"}
_WHT_WORDS = {"wht", "withholding", "retencao", "retencion", "retention", "quellensteuer"}
_COUNTRY_NAMES = {"estonia": "EE", "eesti": "EE", "germany": "DE", "deutschland": "DE", "portugal": "PT",
                  "united kingdom": "GB", "england": "GB", "ghana": "GH", "south africa": "ZA", "kenya": "KE",
                  "malaysia": "MY", "singapore": "SG", "thailand": "TH", "switzerland": "CH", "schweiz": "CH",
                  "denmark": "DK", "danmark": "DK", "poland": "PL", "polska": "PL", "sweden": "SE",
                  "romania": "RO", "vietnam": "VN", "canada": "CA", "united states": "US", "turkey": "TR"}


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").translate(lx._TRANSLIT)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in norm_text(s).split() if t not in _STOP and len(t) > 1]


def norm_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def vat_keys(v: str) -> list[str]:
    k = norm_key(v)
    if not k:
        return []
    out = [k]
    if len(k) > 4 and k[:2].isalpha():
        out.append(k[2:])                       # "EE101234567" == "101234567"
    return out


_ISO2 = set(_COUNTRY_NAMES.values()) | {"AT", "BE", "BG", "HR", "CY", "CZ", "FI", "FR", "GR", "HU", "IE", "IT",
                                         "LV", "LT", "LU", "MT", "NL", "SK", "SI", "ES", "NO"}


def country_of(ir: DocIR) -> str:
    """Supplier country from printed evidence: VAT prefix, then address, then a national
    currency (ISO 4217 codes start with the ISO 3166 country code; EUR is not national)."""
    v = norm_key(ir.supplier.vat_id)
    if len(v) > 4 and v[:2].isalpha() and v[2:4].isdigit():
        return {"EL": "GR"}.get(v[:2], v[:2])
    if ir.supplier.country:
        return ir.supplier.country.upper()
    addr = norm_text(ir.supplier.address)
    for name, cc in _COUNTRY_NAMES.items():
        if re.search(rf"\b{name}\b", addr):
            return cc
    cur = (ir.currency or "").upper()
    if len(cur) == 3 and cur not in {"EUR", "USD", "XAF", "XOF", "XCD", "XPF", "ANG"}:   # not national
        return cur[:2]
    return document_country(ir)


@rule("masters.document_country", "With no supplier evidence, the tax country is the one the document's labelled tax ids and printed country names agree on.")
def document_country(ir: DocIR) -> str:
    """Country hints from the whole document: prefixes of every labelled tax id, and country
    names printed on it. Used only when they point to a single country."""
    hints = set()
    for v in ir.supplier.extra_ids:
        k = norm_key(v)
        if len(k) > 6 and k[:2].isalpha() and k[2:4].isdigit() and k[:2] in _ISO2:
            hints.add(k[:2])
    text = norm_text(ir.text)
    for name, cc in _COUNTRY_NAMES.items():
        if re.search(rf"\b{name}\b", text):
            hints.add(cc)
    return hints.pop() if len(hints) == 1 else ""


class Masters:
    def __init__(self, folder: str):
        def load(name):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                return json.load(fh)
        self._index_suppliers(load("suppliers.json")["suppliers"])
        self._index_taxes(load("tax_master.json")["taxes"])
        self._index_books(load("chart_of_books.json")["companies"])
        self.terms = load("payment_terms.json")["payment_terms"]
        self.term_by_days = defaultdict(list)
        for t in self.terms:
            self.term_by_days[t["days"]].append(t)
        self.pos = {norm_key(p["po_number"]): p for p in load("po_master.json")["purchase_orders"]}

    # ── indexes ──
    def _index_suppliers(self, rows):
        self.sup_by_vat, self.sup_by_iban, self.sup_by_domain = {}, {}, defaultdict(list)
        self.sup_tokens = defaultdict(set)
        self.sup = {}
        for r in rows:
            sid = r["supplier_id"]
            self.sup[sid] = r
            for k in vat_keys(r.get("vat_id", "")):
                self.sup_by_vat[k] = sid
            if r.get("bank_iban"):
                self.sup_by_iban[norm_key(r["bank_iban"])] = sid
            if "@" in r.get("email", ""):
                self.sup_by_domain[r["email"].split("@", 1)[1].lower()].append(sid)
            for t in tokens(r["name"]):
                self.sup_tokens[t].add(sid)

    def _index_taxes(self, rows):
        self.tax_by_cr = defaultdict(list)
        self.tax_by_rate = defaultdict(list)
        for r in rows:
            rate = parse_amount(r["rate"]).normalize()
            self.tax_by_cr[(r["country"], rate)].append(r)
            self.tax_by_rate[rate].append(r)

    def _index_books(self, companies):
        self.book_rows = []
        self.book_tokens = defaultdict(set)
        for c in companies:
            for bu in c["business_units"]:
                for loc in bu["locations"]:
                    i = len(self.book_rows)
                    self.book_rows.append((c, bu, loc))
                    for t in tokens(bu["business_unit_name"]) + tokens(loc["invoice_to_address"]):
                        self.book_tokens[t].add(i)

    # ── resolvers ──
    @rule("masters.supplier_keys", "Suppliers resolve by exact VAT id / IBAN first, then a strong name match in the same country.")
    def supplier(self, ir: DocIR) -> tuple[str, str]:
        s = ir.supplier
        for ident in [s.vat_id, s.iban, *s.extra_ids]:
            for k in vat_keys(ident):
                if k in self.sup_by_vat:
                    return self.sup_by_vat[k], f"VAT id {ident}"
            if ident and norm_key(ident) in self.sup_by_iban:
                return self.sup_by_iban[norm_key(ident)], f"IBAN {ident}"
        best, score = self._by_name(s.name, self.sup_tokens, lambda i: self.sup[i]["name"])
        if best and score >= 0.9:
            cc = country_of(ir)
            if not cc or self.sup[best]["country"] == cc:
                return best, f"name '{s.name}' ~ '{self.sup[best]['name']}' ({score:.2f})"
        if "@" in s.email:
            ids = self.sup_by_domain.get(s.email.split("@", 1)[1].lower(), [])
            if len(ids) == 1:
                return ids[0], f"email domain {s.email}"
        return "", "no supplier match"

    @staticmethod
    def _by_name(name: str, index, label, limit: int = 50):
        toks = tokens(name)
        if not toks:
            return None, 0.0
        # rarest tokens first keeps the candidate set small at scale
        cands = set()
        for t in sorted(toks, key=lambda t: len(index.get(t, ()))):
            cands |= index.get(t, set())
            if len(cands) >= limit:
                break
        target = " ".join(toks)
        scored = [(SequenceMatcher(None, target, " ".join(tokens(label(c)))).ratio(), c) for c in cands]
        if not scored:
            return None, 0.0
        score, best = max(scored)
        return best, score

    @rule("masters.tax_country_rate", "A tax resolves by (country, rate, type); an ambiguous match is an honest blank.")
    def tax_code(self, name: str, rate, country: str) -> tuple[str, str]:
        if rate is None:
            return "", "no rate printed"
        rate = parse_amount(rate)
        if rate is None:
            return "", "no rate printed"
        rate = abs(rate).normalize()
        alpha = lambda s: {w for w in norm_text(s).split() if w.isalpha()}  # noqa: E731
        words = alpha(name)
        # the issuer names its own tax: 'MwSt', 'IVA', 'moms', 'KM' point to its country
        word_cc = set().union(*(lx.TAX_WORD_COUNTRIES.get(w, set()) for w in words)) if words else set()
        if word_cc and country not in word_cc:
            in_master = {t["country"] for t in self.tax_by_rate.get(rate, [])} & word_cc
            if len(in_master) == 1:
                country = in_master.pop()
        pool = self.tax_by_cr.get((country, rate), []) if country else self.tax_by_rate.get(rate, [])

        def fits(t) -> bool:
            ttype = t["tax_type"].upper()
            tw = alpha(t["name"]) | {ttype.lower()}
            if words & _WHT_WORDS:
                return ttype == "WHT"
            if ttype == "WHT":
                return False
            if words & (tw - {"vat", "tax", "levy"} - set(norm_text(t["country"]).split())):
                return True
            return ttype in _VAT_TYPES and (not words or bool(words & _VAT_WORDS) or words <= {"tax"})

        hits = [t for t in pool if fits(t)]
        if len(hits) == 1:
            return hits[0]["code"], f"{hits[0]['name']} ({'country ' + country if country else 'unique rate'})"
        named_other = bool(words - _VAT_WORDS - {"tax", "total", "zzgl", "incl", "includes", "plus"})
        if not hits and len(pool) == 1 and country and not (words & _WHT_WORDS) and not named_other:
            return pool[0]["code"], f"{pool[0]['name']} (only {fmt_rate(rate)}% tax in {country})"
        return "", f"{len(hits)} candidates for {name or 'tax'} {fmt_rate(rate)}% {country or '(country unknown)'}"

    @rule("masters.buyer_by_address", "The buyer resolves by its invoice-to address (postcode with city or country printed together); only codes that are unique are filled.")
    def buyer(self, ir: DocIR) -> tuple[dict, str]:
        blank = {"company_code": "", "business_unit_code": "", "location_code": ""}
        best, score = self._by_name(ir.buyer.name, self.book_tokens,
                                    lambda i: self.book_rows[i][1]["business_unit_name"])
        if best is not None and score >= 0.9:
            c, bu, loc = self.book_rows[best]
            return ({"company_code": c["company_code"], "business_unit_code": bu["business_unit_code"],
                     "location_code": loc["location_code"]}, f"buyer '{ir.buyer.name}' ~ '{bu['business_unit_name']}'")
        hits = self._by_address(ir.buyer.address + "\n" + ir.text)
        if not hits:
            return blank, "no buyer match"
        rows = [self.book_rows[i] for i in hits]
        pick = lambda f: {f(r) for r in rows}.pop() if len({f(r) for r in rows}) == 1 else ""  # noqa: E731
        out = {"company_code": pick(lambda r: r[0]["company_code"]),
               "business_unit_code": pick(lambda r: r[1]["business_unit_code"]),
               "location_code": pick(lambda r: r[2]["location_code"])}
        if not out["business_unit_code"] and len(rows) > 1:
            # several units share the address: the buyer's own name may name one ('... Holdings ...')
            shared = set.intersection(*(set(tokens(r[1]["business_unit_name"])) for r in rows))
            name = set(tokens(ir.buyer.name)) | set(tokens(ir.text[:2000]))
            named = [r for r in rows if (set(tokens(r[1]["business_unit_name"])) - shared) & name]
            if len(named) == 1:
                out["business_unit_code"] = named[0][1]["business_unit_code"]
        why = f"invoice-to address of {', '.join(sorted({r[2]['location_code'] for r in rows}))}"
        return out, why + ("" if out["business_unit_code"] else "; business unit ambiguous, left blank")

    def _by_address(self, text: str) -> list[int]:
        return sorted({i for i, _ in self._address_hits(text)})

    def _address_hits(self, text: str) -> list[tuple[int, int]]:
        """(location row, line index) where a location's postcode is printed with its city or
        country on the same or next line."""
        lines = [norm_text(l) for l in text.splitlines()]
        hits = []
        for i, (c, bu, loc) in enumerate(self.book_rows):
            addr = norm_text(loc["invoice_to_address"])
            pc = re.search(r"\b\d{4,6}\b", addr)
            if not pc:
                continue
            places = [w for w in addr.split() if w.isalpha() and len(w) > 3 and w not in pc.group(0)]
            for k, ln in enumerate(lines):
                if re.search(rf"\b{pc.group(0)}\b", ln):
                    near_text = " ".join(lines[max(0, k - 1):k + 2])
                    if any(re.search(rf"\b{p}\b", near_text) for p in places[-2:]):
                        hits.append((i, k))
        return hits

    @rule("masters.supplier_is_not_buyer", "A company named in the block above the buyer's invoice-to address is the buyer; the supplier is another company on the page.")
    def supplier_not_buyer(self, ir: DocIR, payable: dict) -> str:
        """Repair a supplier name/id that was read from the buyer's own address block."""
        hits = self._address_hits(ir.text)
        if not hits:
            return ""
        lines = ir.text.splitlines()
        block = {j for _, k in hits for j in range(max(0, k - 4), k + 2)}
        block_text = norm_text(" ".join(lines[j] for j in sorted(block) if j < len(lines)))
        in_block = lambda s: bool(s) and norm_text(s)[:15] in block_text  # noqa: E731
        notes = []
        sup = payable["supplier"]
        if in_block(sup["name"]):
            buyer_first = norm_text(sup["name"]).split()[0]
            alt = next((a for a in ir.supplier.alternatives
                        if not in_block(a) and norm_text(a).split()[:1] != [buyer_first]), "")
            notes.append(f"'{sup['name']}' is printed in the buyer's address block; supplier name "
                         f"{'-> ' + repr(alt) if alt else 'left blank (no other company on the page)'}")
            sup["name"] = alt
        if sup["vat_id"] and norm_text(sup["vat_id"]) in block_text:
            other = next((v for v in ir.supplier.extra_ids if norm_text(v) not in block_text
                          and len(v) < 16 and re.search(r"\d{6}", v)), "")
            notes.append(f"tax id {sup['vat_id']} is the buyer's; supplier tax id -> {other or 'blank'}")
            sup["vat_id"] = other
        return "; ".join(notes)

    @rule("masters.payment_terms", "Payment terms resolve from the terms text, else from due date minus invoice date.")
    def payment_term(self, ir: DocIR) -> tuple[str, str]:
        text = norm_text(ir.payment_terms_text)
        if text:
            hits = [t for t in self.terms for a in t["text_aliases"] if re.search(rf"\b{re.escape(a)}\b", text)]
            ids = {t["payment_term_id"] for t in hits}
            if len(ids) == 1:
                return ids.pop(), f"terms text '{ir.payment_terms_text}'"
            nums = {int(n) for n in re.findall(r"\b(\d{1,3})\b", text)}
            by_days = [t for n in nums for t in self.term_by_days.get(n, []) if t["days"] > 0]
            if len({t["payment_term_id"] for t in by_days}) == 1:
                return by_days[0]["payment_term_id"], f"{by_days[0]['days']} days in terms text"
        try:
            days = (date.fromisoformat(ir.due_date) - date.fromisoformat(ir.invoice_date)).days
        except ValueError:
            return "", "no payment term evidence"
        cands = [t for t in self.term_by_days.get(days, []) if t["days"] > 0 or days == 0]
        if days == 0 and "advance" not in norm_text(ir.payment_terms_text):
            # due on the invoice date is immediate payment, not payment in advance
            cands = [t for t in cands if "immediate" in t["text_aliases"]]
        if len(cands) == 1:
            return cands[0]["payment_term_id"], f"due date is invoice date + {days} days"
        return "", f"no unique term for {days} days"

    def po(self, ir: DocIR) -> tuple[str, str]:
        p = self.pos.get(norm_key(ir.po_number)) if ir.po_number else None
        return (p["po_id"], "PO number in master") if p else ("", "PO not in master" if ir.po_number else "no PO printed")

    def resolve(self, ir: DocIR, payable: dict) -> list[str]:
        """Fill every master-data code in place; return the evidence for each decision."""
        ev = []
        sid, why = self.supplier(ir)
        payable["supplier"]["supplier_id"] = sid
        ev.append(f"supplier_id={sid or '∅'}: {why}")
        payable["buyer"], why = self.buyer(ir)
        ev.append(f"buyer: {why}")
        repaired = self.supplier_not_buyer(ir, payable)
        if repaired:
            ev.append(f"supplier: {repaired}")
        payable["payment_term_id"], why = self.payment_term(ir)
        ev.append(f"payment_term_id={payable['payment_term_id'] or '∅'}: {why}")
        payable["po_id"], why = self.po(ir)
        ev.append(f"po_id={payable['po_id'] or '∅'}: {why}")
        cc = self.sup[sid]["country"] if sid else country_of(ir)
        taxes = list(payable["taxes"]) + [t for li in payable["line_items"] for t in li["taxes"]]
        for t in taxes:
            code, why = self.tax_code(t["tax_name"], t["tax_rate"], cc)
            t["tax_type_code"] = code
            if code:
                t["tax_type"] = next(r["tax_type"] for r in self.tax_by_rate[abs(parse_amount(t["tax_rate"])).normalize()]
                                     if r["code"] == code)
            ev.append(f"tax {t['tax_name']} {t['tax_rate']}%={code or '∅'}: {why}")
        return ev


def fmt_rate(r) -> str:
    s = format(r, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s
