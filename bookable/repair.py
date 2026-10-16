"""Optional repair step: Claude re-reads a payable that the deterministic reader could not reconcile.

Off by default (`python run.py --repair claude`). Only payables that still fail after the second
OCR reading are sent, and only their pages. The model returns a TRANSCRIPTION (lines, taxes,
charges, totals) in a fixed JSON schema — never an autodraft — and is never told which number
the components should reach; it gets the system's current reading and a target-less fact
("the components do not add up to any printed total").

Two gates keep the repair honest:
  * grounding — every number the model returns must be printed on those pages, or the whole
    reply is rejected (rule 1 of the brief, enforced in code rather than trusted);
  * verification — the corrected transcription goes through the same structure search and is
    booked by the sealed erp.py; it is kept only if it reconciles, else the record stays flagged.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
from decimal import Decimal

from .ir import Charge, DocIR, Line, TaxRow
from .numbers import parse_amount
from .rules import rule

MODEL = os.environ.get("BOOKABLE_REPAIR_MODEL", "claude-opus-5-5")
PRICE_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0)}          # input, output (USD), for estimates

_SYSTEM = """You transcribe supplier documents for an accounts-payable system.

You receive the pages of ONE payable and a machine transcription that does not add up: its line
items, taxes and charges do not reproduce any total printed on the page. Re-read the pages and
return a corrected transcription of what is PRINTED.

Rules:
- Copy numbers exactly as printed (convert to dot-decimal, no thousands separators). Never compute,
  derive, round or invent a number. If a value is not printed, use null.
- One entry per billed line. Do not list a subtotal, a total, a tax line or a page carry-over as a
  line. Lines that only restate other lines (a summary of the same charges) are not billed twice.
- taxes: each tax as printed (name, rate in percent, base if printed, amount if printed).
  withholding=true only if the page says the tax is withheld/deducted.
- charges: freight, insurance, other fees or deductions printed outside the line table
  (bucket: freight | insurance | extra | excise); a deduction already paid/credited is negative.
- total: the amount the document says is owed, as printed.
- notes: one short sentence on what the machine transcription got wrong.
"""

_NUM = {"type": ["number", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "quantity": _NUM, "unit_price": _NUM,
                           "total": _NUM, "discount_percentage": _NUM, "discount_amount": _NUM,
                           "tax_rate": _NUM, "tax_amount": _NUM},
            "required": ["description", "quantity", "unit_price", "total", "discount_percentage",
                         "discount_amount", "tax_rate", "tax_amount"],
            "additionalProperties": False}},
        "taxes": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "rate": _NUM, "base": _NUM, "amount": _NUM,
                           "withholding": {"type": "boolean"}},
            "required": ["name", "rate", "base", "amount", "withholding"], "additionalProperties": False}},
        "charges": {"type": "array", "items": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "amount": {"type": "number"},
                           "bucket": {"type": "string", "enum": ["freight", "insurance", "extra", "excise"]}},
            "required": ["label", "amount", "bucket"], "additionalProperties": False}},
        "header_discount": _NUM, "subtotal": _NUM, "total_tax": _NUM, "total": _NUM,
        "prices_include_tax": {"type": "boolean"},
        "notes": {"type": "string"},
    },
    "required": ["lines", "taxes", "charges", "header_discount", "subtotal", "total_tax", "total",
                 "prices_include_tax", "notes"],
    "additionalProperties": False,
}


def load_key(name: str = "ANTHROPIC_API_KEY") -> bool:
    """An API key from the environment, else from a git-ignored .env next to run.py."""
    if os.environ.get(name):
        return True
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env):
        with open(env, encoding="utf-8") as fh:
            for raw in fh:
                k, _, v = raw.strip().partition("=")
                if k.strip() == name and v.strip():
                    os.environ[name] = v.strip().strip('"').strip("'")
                    return True
    return False


def printed_numbers(page_texts: list[str]) -> set[Decimal]:
    """Every number printed on the pages, read in both decimal conventions."""
    out: set[Decimal] = set()
    for text in page_texts:
        # plain numbers, plus the one legitimate spaced form: thousands groups ('1 069.00')
        toks = re.findall(r"-?\d[\d.,']*\d|\d", text) + re.findall(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?", text)
        for tok in toks:
            # OCR often reads a decimal comma as an apostrophe ('39'39' printed as 39,39)
            variants = {tok} | ({tok.replace("'", ","), tok.replace("'", ".")} if "'" in tok else set())
            for t in variants:
                for sep in (".", ","):
                    v = parse_amount(t, sep)
                    if v is not None:
                        out.add(abs(v).normalize())
    return out


@rule("repair.grounded_numbers", "A model's transcription is accepted only if every number in it is printed on the pages it read.")
def ungrounded(reply: dict, printed: set[Decimal]) -> list[str]:
    """Numbers in the reply that do not appear on the page. Quantity 1 may be implied."""
    bad = []

    def check(v, where, allow_one=False):
        if v is None:
            return
        d = abs(Decimal(str(v))).normalize()
        if d in printed or (allow_one and d == 1) or d == 0:
            return
        bad.append(f"{where}={v}")

    for i, li in enumerate(reply.get("lines", [])):
        check(li.get("quantity"), f"lines[{i}].quantity", allow_one=True)
        for k in ("unit_price", "total", "discount_percentage", "discount_amount", "tax_rate", "tax_amount"):
            check(li.get(k), f"lines[{i}].{k}")
    for i, t in enumerate(reply.get("taxes", [])):
        for k in ("rate", "base", "amount"):
            check(t.get(k), f"taxes[{i}].{k}")
    for i, c in enumerate(reply.get("charges", [])):
        check(c.get("amount"), f"charges[{i}].amount")
    for k in ("header_discount", "subtotal", "total_tax", "total"):
        check(reply.get(k), k)
    return bad


def to_ir(reply: dict, base: DocIR) -> DocIR:
    """The model's transcription in place of the reader's, keeping the header fields."""
    d = lambda v: None if v is None else Decimal(str(v))  # noqa: E731
    ir = copy.deepcopy(base)
    ir.lines = [Line(description=li["description"], quantity=d(li["quantity"]), unit_price=d(li["unit_price"]),
                     total=d(li["total"]), discount_pct=d(li["discount_percentage"]),
                     discount_amount=d(li["discount_amount"]), tax_rate=d(li["tax_rate"]),
                     tax_amount=d(li["tax_amount"])) for li in reply["lines"]]
    ir.tax_rows = [TaxRow(t["name"], d(t["rate"]), base=d(t["base"]), amount=d(t["amount"]),
                          withholding=bool(t["withholding"])) for t in reply["taxes"]]
    ir.charges = [Charge(c["label"], d(c["amount"]), c["bucket"]) for c in reply["charges"]]
    ir.header_discount = d(reply["header_discount"])
    ir.subtotal, ir.total_tax = d(reply["subtotal"]), d(reply["total_tax"])
    ir.gross = d(reply["total"]) if reply["total"] is not None else base.gross
    ir.prices_include_tax = bool(reply["prices_include_tax"]) or None
    return ir


def page_pdf(pdf: str, pages: list[int]) -> bytes:
    import pymupdf
    with pymupdf.open(pdf) as src, pymupdf.open() as dst:
        for p in pages:
            dst.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        return dst.tobytes()


def estimate(pages: int) -> tuple[int, int, float]:
    """Rough tokens and USD for one repair call (a page is ~2,000 input tokens)."""
    tin, tout = 2500 + 2000 * pages, 2500
    pin, pout = PRICE_PER_MTOK.get(MODEL, (4.0, 20.0))
    return tin, tout, (tin * pin + tout * pout) / 1e6


class GroqRepair:
    """Groq (OpenAI-compatible chat API). The models offered to this key are text-only, so the
    model gets the page's OCR text laid out as rows of '|'-separated cells instead of the image.
    Plain HTTP via the standard library: no extra dependency."""
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str = os.environ.get("BOOKABLE_GROQ_MODEL", "openai/gpt-oss-120b")):
        self.model = model
        self.calls: list[dict] = []
        self.disabled = ""

    def _post(self, body: dict) -> dict:
        import urllib.request
        req = urllib.request.Request(self.URL, json.dumps(body).encode(), {
            "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json",
            "User-Agent": "bookable-payable/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())

    def _post_with_backoff(self, body: dict, retries: int = 3, sleep=None) -> dict:
        """Rate limits (HTTP 429) say how long to wait: wait that long (at most 60 s) and retry."""
        import time
        import urllib.error
        sleep = sleep or time.sleep
        for attempt in range(retries + 1):
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == retries:
                    raise
                detail = e.read().decode("utf-8", "replace")
                m = re.search(r"try again in ([\d.]+)\s*(ms|s)", detail)
                wait = float(m.group(1)) / (1000 if m and m.group(2) == "ms" else 1) if m else \
                    float(e.headers.get("retry-after", "5") if e.headers else 5)
                self.calls.append({"provider": self.name, "note": f"rate limited; waiting {min(wait + 1, 60):.1f}s"})
                sleep(min(wait + 1, 60))
        raise RuntimeError("unreachable")

    def ask(self, pdf: str, pages: list[int], current: dict, facts: list[str],
            page_texts: list[str] | None = None) -> dict | None:
        import urllib.error
        if self.disabled:
            self.calls.append({"provider": self.name, "error": f"skipped: {self.disabled}"})
            return None
        user = ("PAGES (OCR text; each line is one printed row, '|' separates cells):\n\n"
                + "\n\n".join(page_texts or [])
                + "\n\nMachine transcription (does not add up):\n"
                + json.dumps(current, ensure_ascii=False, sort_keys=True)
                + "\n\nWhat the system observed:\n- " + "\n- ".join(facts)
                + "\n\nReturn the corrected transcription as JSON.")
        base = {"model": self.model, "temperature": 0, "max_completion_tokens": 8000,
                "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]}
        # strict JSON schema first; models without schema support get JSON mode + the schema in the prompt
        attempts = [dict(base, response_format={"type": "json_schema", "json_schema": {
                        "name": "transcription", "strict": True, "schema": SCHEMA}}),
                    dict(base, messages=[{"role": "system", "content": _SYSTEM + "\nJSON schema:\n"
                                          + json.dumps(SCHEMA)}, base["messages"][1]],
                         response_format={"type": "json_object"})]
        for body in attempts:
            try:
                data = self._post_with_backoff(body)
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace")
                self.calls.append({"provider": self.name, "error": f"HTTP {e.code}: {detail}"})
                if e.code in (401, 403):
                    self.disabled = f"HTTP {e.code}: key rejected"
                    return None
                if e.code == 400:
                    continue                          # try the next request form
                return None
            except (urllib.error.URLError, TimeoutError) as e:
                self.calls.append({"provider": self.name, "error": f"connection: {e}"})
                return None
            u = data.get("usage", {})
            choice = data["choices"][0]
            self.calls.append({"provider": self.name, "request_id": data.get("id"), "model": data.get("model"),
                               "stop": choice.get("finish_reason"), "input_tokens": u.get("prompt_tokens", 0),
                               "output_tokens": u.get("completion_tokens", 0)})
            if choice.get("finish_reason") != "stop":
                return None
            try:
                reply = json.loads(choice["message"]["content"])
            except (ValueError, TypeError):
                return None
            return reply if _shape_ok(reply) else None
        return None


def _shape_ok(reply: dict) -> bool:
    """JSON mode does not enforce the schema: check the fields the repair needs."""
    return isinstance(reply, dict) and all(k in reply for k in SCHEMA["required"]) \
        and isinstance(reply["lines"], list) and isinstance(reply["taxes"], list) and isinstance(reply["charges"], list)


class ClaudeRepair:
    name = "claude"

    def __init__(self, effort: str = "high", client=None):
        self.effort = effort
        self._client = client
        self.calls: list[dict] = []
        self.disabled = ""               # set after an account error; later calls are skipped

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def ask(self, pdf: str, pages: list[int], current: dict, facts: list[str],
            page_texts: list[str] | None = None) -> dict | None:
        """One repair call. Returns the parsed transcription, or None (refusal / error)."""
        import anthropic
        if self.disabled:
            self.calls.append({"provider": self.name, "error": f"skipped: {self.disabled}"})
            return None
        data = base64.standard_b64encode(page_pdf(pdf, pages)).decode()
        user = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": "Machine transcription (does not add up):\n"
                                     + json.dumps(current, ensure_ascii=False, sort_keys=True)
                                     + "\n\nWhat the system observed:\n- " + "\n- ".join(facts)
                                     + "\n\nReturn the corrected transcription."},
        ]
        try:
            resp = self.client().beta.messages.create(
                model=MODEL, max_tokens=16000,
                system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APIStatusError as e:
            self.calls.append({"provider": self.name, "error": f"{type(e).__name__} {e.status_code}: {e.message}"})
            # account problems will not go away for the next document: stop trying
            if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) \
                    or "credit balance" in str(e.message).lower():
                self.disabled = f"{type(e).__name__}: account cannot use the API ({e.status_code})"
            return None
        except anthropic.APIConnectionError as e:
            self.calls.append({"provider": self.name, "error": f"connection: {e}"})
            return None
        u = resp.usage
        self.calls.append({"provider": self.name, "request_id": resp._request_id, "model": resp.model,
                           "stop": resp.stop_reason,
                           "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                           "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0})
        if resp.stop_reason != "end_turn":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            return None
