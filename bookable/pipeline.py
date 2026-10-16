"""One PDF -> {"file", "payables", "declined"} plus an audit trail.

Reading strategy per page:
  1. the PDF's own text layer when it has one (exact);
  2. otherwise the primary OCR engine (PP-OCR via PaddleOCR/RapidOCR by default, or Docling);
  3. a page is "difficult" when its payable does not reconcile or its line arithmetic does
     not close; it is then read again by a second reader (PP-OCR at 300 dpi by default, or
     GOT-OCR 2.0) and the reading whose document reconciles better is kept. Readings are
     chosen between, never blended or edited to fit.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import traceback
from dataclasses import asdict

from . import assemble
from . import lexicon as lx
from .masters import Masters
from .numbers import detect_decimal_sep
from .ocr import readers
from .ocr.layout import PageLayout
from .solve import solve
from .understand import PageInfo, understand

CACHE = os.environ.get("BOOKABLE_CACHE", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache"))


def cached(pdf: str, engine: str, page_no: int, compute):
    """OCR is the expensive step; its result depends only on the file bytes, engine and page."""
    with open(pdf, "rb") as fh:
        digest = hashlib.sha1(fh.read()).hexdigest()[:16]
    path = os.path.join(CACHE, digest, f"{engine}-{page_no}.pkl")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return pickle.load(fh)
    value = compute()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(value, fh)
    return value


class Pipeline:
    SECOND_READERS = {
        "pp-hires": ("PP-OCR at 300 dpi", "paddle300", lambda pdf, no: readers.paddle_read(pdf, no, dpi=300)),
        "got": ("GOT-OCR 2.0", "got", readers.got_read),
    }

    def __init__(self, masters_dir: str, engine: str = "paddle", second_reader: str = "pp-hires", log=print,
                 classifier=None, repairers=None, repair_dry_run=False):
        self.masters = Masters(masters_dir)
        self.classifier = classifier             # optional local model, only for pages declined as OTHER
        self.repairers = list(repairers or [])   # optional repair chain, tried in order (repair.py)
        self.repair_dry_run = repair_dry_run
        self.planned: list[dict] = []
        self.engine = engine
        self.second_reader = second_reader if second_reader in self.SECOND_READERS else None
        self.log = log

    # ── reading ──
    def read_pages(self, pdf: str) -> list[PageLayout]:
        n = readers.page_count(pdf)
        layouts: dict[int, PageLayout] = {}
        for no in range(1, n + 1):
            tl = readers.text_layer(pdf, no)
            if tl:
                layouts[no] = tl
        missing = [no for no in range(1, n + 1) if no not in layouts]
        if self.engine == "docling" and missing:
            docl = cached(pdf, "docling", 0, lambda: readers.docling_read(pdf))
            layouts.update({pl.page_no: pl for pl in docl if pl.page_no in missing})
        else:
            read = readers.paddle_read if self.engine == "paddle" else readers.got_read
            for no in missing:
                key = self.engine + os.environ.get("BOOKABLE_OCR_BACKEND", "")
                layouts[no] = cached(pdf, key, no, lambda no=no: read(pdf, no))
        return [layouts[k] for k in sorted(layouts)]

    @staticmethod
    def understand_all(layouts: list[PageLayout]) -> list[PageInfo]:
        """Each page keeps its own number locale (an EUR invoice and a Portuguese customs sheet
        can share one PDF); the document's majority only decides pages that are ambiguous."""
        doc_dec = detect_decimal_sep(w.text for pl in layouts for w in pl.words)
        return [understand(pl, detect_decimal_sep(w.text for w in pl.words) or doc_dec) for pl in layouts]

    # ── one file ──
    def process(self, pdf: str) -> tuple[dict, dict]:
        name = os.path.basename(pdf)
        layouts = self.read_pages(pdf)
        infos = self.understand_all(layouts)
        out, audit = self._solve_file(name, infos)

        if self.second_reader and self._difficult(audit):
            hard = sorted({p for d in audit["payables"] if not d["reconciled"] for p in d["pages"]}
                          | {i.page_no for i in infos if i.lines and i.consistency < 0.5})
            hard = [p for p in hard if p in {pl.page_no for pl in layouts if pl.source != "text-layer"}]
            label, key, read = self.SECOND_READERS[self.second_reader]
            if hard:
                self.log(f"  {name}: second reading of page(s) {hard} with {label}")
                try:
                    alt_layouts = [cached(pdf, key, pl.page_no, lambda pl=pl: read(pdf, pl.page_no))
                                   if pl.page_no in hard else pl for pl in layouts]
                    alt_infos = self.understand_all(alt_layouts)
                    alt_out, alt_audit = self._solve_file(name, alt_infos)
                    if self._better(alt_audit, audit):
                        alt_audit["notes"] = [f"pages {hard} re-read with {label}; that reading reconciles better"]
                        alt_layouts_used = alt_layouts
                        out, audit, layouts = alt_out, alt_audit, alt_layouts_used
                    else:
                        audit["notes"].append(f"{label} re-read of pages {hard} did not improve; primary reading kept")
                except Exception as e:                      # second reader is optional
                    audit["notes"].append(f"{label} unavailable: {type(e).__name__}: {e}")
        if self.repairers:
            self._repair(pdf, layouts, out, audit)
        audit.pop("_irs", None)
        audit["readers"] = {pl.page_no: pl.source for pl in layouts}
        return out, audit

    def _repair(self, pdf: str, layouts: list[PageLayout], out: dict, audit: dict) -> None:
        """Send each payable that still does not reconcile to the repair model (see repair.py)."""
        from . import repair
        for k, (ap, ir) in enumerate(zip(audit["payables"], audit.get("_irs", []))):
            if ap["reconciled"]:
                continue
            pages = ap["pages"]
            tin, tout, usd = repair.estimate(len(pages))
            if self.repair_dry_run:
                self.planned.append({"file": out["file"], "pages": pages, "input_tokens": tin,
                                     "output_tokens": tout, "usd": round(usd, 4)})
                continue
            current = {"lines": [{"description": li.description[:80], "quantity": _s(li.quantity),
                                  "unit_price": _s(li.unit_price), "total": _s(li.total),
                                  "tax_rate": _s(li.tax_rate)} for li in ir.lines],
                       "taxes": [{"name": t.name, "rate": _s(t.rate), "amount": _s(t.amount)} for t in ir.tax_rows],
                       "charges": [{"label": c.label, "amount": _s(c.amount)} for c in ir.charges]}
            facts = ["the line items, taxes and charges above do not add up to any total printed on the page"]
            facts += [f["message"] for f in ap["facts"] if f["kind"] in ("line_mismatch", "tax_base", "scale")]
            texts = [_cells_text(pl) for pl in layouts if pl.page_no in pages]
            printed = repair.printed_numbers([pl.text() for pl in layouts if pl.page_no in pages] + texts)
            for provider in self.repairers:           # the chain: first grounded, reconciling reply wins
                reply = provider.ask(pdf, pages, current, facts, page_texts=texts)
                call = provider.calls[-1] if provider.calls else {}
                tag = f"{provider.name} {call.get('request_id') or ''}".strip()
                if reply is None:
                    ap["notes"].append(f"repair [{tag}]: no usable reply ({call.get('error') or call.get('stop')})")
                    continue
                bad = repair.ungrounded(reply, printed)
                if bad:
                    ap["notes"].append(f"repair [{tag}] rejected: numbers not printed on the page {bad[:6]}")
                    continue
                new_ir = repair.to_ir(reply, ir)
                r = solve(new_ir)
                if not r.reconciled:
                    ap["notes"].append(f"repair [{tag}] did not reconcile (books {r.booked} vs printed "
                                       f"{r.target}); not used")
                    continue
                evidence = self.masters.resolve(new_ir, r.payable)
                out["payables"][k] = r.payable
                ap.update({"reconciled": True, "booked": str(r.booked), "printed_gross": str(r.target),
                           "structure": r.chosen, "alternatives": r.alternatives,
                           "facts": [asdict(f) for f in r.facts], "master_data": evidence})
                ap["notes"].append(f"repaired by {call.get('model')} [{tag}]: {reply.get('notes', '')} "
                                   f"(grounded, re-verified by erp_book)")
                break

    def _solve_file(self, name: str, infos: list[PageInfo]) -> tuple[dict, dict]:
        groups, declined = assemble.split(infos, name)
        out = {"file": name, "payables": [], "declined": declined}
        audit = {"file": name, "payables": [], "notes": []}
        for d in declined:
            text = d.pop("_text", "")
            if self.classifier and d["doc_type"] == "OTHER" and text.strip():
                label = self.classifier.page_type(text)
                audit["notes"].append(f"local model on declined pages ({d['reason'][:20]}...): {label}")
                if label in lx.PAYABLE_TYPES:
                    d["reason"] += f"; a local model reads it as {label}, but no provable amounts were found, so it is not booked"
                elif label and label != "OTHER":
                    d["doc_type"] = label
                    d["reason"] = d["reason"].split(":")[0] + f": {assemble._REASONS.get(label, assemble._REASONS['OTHER'])} (type named by a local model)"
        for ir, notes in groups:
            try:
                r = solve(ir)
            except Exception as e:
                out["declined"].append({"doc_type": ir.doc_type,
                                        "reason": f"pages {ir.pages}: could not build a payable ({e})"})
                audit["notes"].append(traceback.format_exc(limit=2))
                continue
            evidence = self.masters.resolve(ir, r.payable)
            out["payables"].append(r.payable)
            audit.setdefault("_irs", []).append(ir)
            audit["payables"].append({
                "invoice_number": ir.invoice_number, "pages": ir.pages, "reconciled": r.reconciled,
                "gross_inferred": ir.gross_inferred,
                "booked": str(r.booked), "printed_gross": str(r.target), "structure": r.chosen,
                "alternatives": r.alternatives, "notes": notes,
                "facts": [asdict(f) for f in r.facts], "master_data": evidence,
            })
        return out, audit

    @staticmethod
    def _difficult(audit: dict) -> bool:
        return any(not p["reconciled"] for p in audit["payables"]) or not audit["payables"]

    @staticmethod
    def _better(a: dict, b: dict) -> bool:
        score = lambda x: (sum(p["reconciled"] for p in x["payables"]), len(x["payables"]))  # noqa: E731
        return score(a) > score(b)


def _s(v) -> str | None:
    return None if v is None else str(v)


def _cells_text(pl: PageLayout) -> str:
    """A page as rows of '|'-separated cells, for a text-only repair model."""
    from .understand import cells_of
    rows = [" | ".join(c.text for c in cells_of(ln)) for ln in pl.lines()]
    return f"[page {pl.page_no}]\n" + "\n".join(rows)
