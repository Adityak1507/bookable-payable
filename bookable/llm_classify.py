"""Optional second opinion on page type from a small local model (via Ollama).

Used ONLY for pages whose type the deterministic reader could not establish (no title found).
The model proposes a type from the closed list; whether a page becomes a payable is still
decided by the same arithmetic as everywhere else, and the model never touches an amount.
Off by default:  python run.py --classifier ollama[:model]

Prompt layout is cache-friendly: a fixed instruction + label prefix, the page text last.
The answer is constrained by a JSON schema to one label from the list.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import lexicon as lx
from .rules import rule

LABELS = [t for t, _ in lx.DOC_TYPES] + ["OTHER"]
_PREFIX = (
    "You classify one page of a business document received by an accounts-payable team.\n"
    "Answer with exactly one label:\n"
    "INVOICE: a bill demanding payment (tax invoice, invoice, bill without a title but with priced lines and a total)\n"
    "CREDIT_NOTE: a credit note / credit memo\n"
    "PROFORMA: a pro-forma invoice (payment requested before supply)\n"
    "DUNNING: a payment reminder about invoices already sent\n"
    "DELIVERY_NOTE: a delivery note / packing list, quantities without an amount owed\n"
    "CUSTOMS_DECLARATION: a customs declaration or customs detail sheet\n"
    "WAYBILL: an air waybill, bill of lading or consignment note\n"
    "REMITTANCE: a payment advice / remittance slip\n"
    "STATEMENT: a statement of account\n"
    "ORDER: a purchase order, quotation or estimate\n"
    "FORM: a form or questionnaire\n"
    "OTHER: anything else (letters, terms and conditions, signature pages, blank pages)\n"
    "\nPAGE TEXT:\n"
)


class LocalClassifier:
    def __init__(self, model: str = "qwen2.5-coder:7b", url: str = "http://localhost:11434", max_chars: int = 2500):
        self.model, self.url, self.max_chars = model, url.rstrip("/"), max_chars
        self.calls: list[dict] = []

    @rule("llm.page_type_second_opinion", "A local model may name the type of an untitled page from a closed list; it never decides amounts.")
    def page_type(self, page_text: str) -> str | None:
        body = json.dumps({
            "model": self.model, "stream": False, "prompt": _PREFIX + page_text[: self.max_chars],
            "options": {"temperature": 0, "num_predict": 20},
            "format": {"type": "object", "properties": {"label": {"type": "string", "enum": LABELS}},
                       "required": ["label"]},
        }).encode()
        req = urllib.request.Request(self.url + "/api/generate", body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                label = json.loads(json.loads(resp.read())["response"]).get("label")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            self.calls.append({"error": f"{type(e).__name__}: {e}"})
            return None
        self.calls.append({"label": label})
        return label if label in LABELS else None
