"""The optional Claude repair step, with a fake client (no network, no cost)."""
import json
import os
import sys
import unittest
from decimal import Decimal as D
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable import repair  # noqa: E402
from bookable.ir import DocIR, Line  # noqa: E402
from bookable.rules import covers  # noqa: E402
from bookable.solve import solve  # noqa: E402

PAGE = """Hours    Rate    Amount
Consulting   10   100.00   1,000.00
Travel   1   250.00   250.00
VAT 20%   250.00
Total due   1,500.00"""

GOOD = {"lines": [{"description": "Consulting", "quantity": 10, "unit_price": 100.0, "total": 1000.0,
                   "discount_percentage": None, "discount_amount": None, "tax_rate": None, "tax_amount": None},
                  {"description": "Travel", "quantity": 1, "unit_price": 250.0, "total": 250.0,
                   "discount_percentage": None, "discount_amount": None, "tax_rate": None, "tax_amount": None}],
        "taxes": [{"name": "VAT", "rate": 20, "base": None, "amount": 250.0, "withholding": False}],
        "charges": [], "header_discount": None, "subtotal": None, "total_tax": None, "total": 1500.0,
        "prices_include_tax": False, "notes": "travel line was missed"}


def fake_client(reply: dict, stop="end_turn"):
    resp = SimpleNamespace(stop_reason=stop, model="claude-opus-5-5", _request_id="req_test",
                           content=[SimpleNamespace(type="text", text=json.dumps(reply))],
                           usage=SimpleNamespace(input_tokens=3000, output_tokens=400, cache_read_input_tokens=0))
    seen = {}

    def create(**kw):
        seen.update(kw)
        return resp
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), seen


class Grounding(unittest.TestCase):
    @covers("repair.grounded_numbers")
    def test_numbers_must_be_printed(self):
        printed = repair.printed_numbers([PAGE])
        self.assertEqual(repair.ungrounded(GOOD, printed), [])
        invented = json.loads(json.dumps(GOOD))
        invented["charges"] = [{"label": "rounding", "amount": 0.37, "bucket": "extra"}]   # a plug
        self.assertEqual(repair.ungrounded(invented, printed), ["charges[0].amount=0.37"])

    def test_grounded_repair_is_re_verified_by_erp_book(self):
        base = DocIR(currency="EUR", gross=D("1500.00"),
                     lines=[Line(description="Consulting", quantity=D(10), unit_price=D("100.00"), total=D("1000.00"))])
        self.assertFalse(solve(base).reconciled)                      # the reader missed a line
        fixed = repair.to_ir(GOOD, base)
        self.assertTrue(solve(fixed).reconciled)


class Request(unittest.TestCase):
    def test_request_is_target_less_structured_and_cache_friendly(self):
        try:
            import anthropic  # noqa: F401
            import pymupdf
        except ImportError:
            self.skipTest("anthropic / PyMuPDF not installed")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Consulting 10 100.00 1,000.00")
        path = os.path.join(os.path.dirname(__file__), "_tmp_repair.pdf")
        doc.save(path)
        try:
            client, seen = fake_client(GOOD)
            r = repair.ClaudeRepair(client=client)
            reply = r.ask(path, [1], {"lines": []}, ["the line items do not add up to any printed total"])
        finally:
            os.remove(path)
        self.assertEqual(reply["total"], 1500.0)
        self.assertEqual(seen["model"], "claude-opus-5-5")
        self.assertEqual(seen["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(seen["system"][0]["cache_control"], {"type": "ephemeral"})   # fixed prefix cached
        user_text = seen["messages"][0]["content"][1]["text"]
        self.assertNotIn("1500", user_text)                          # the target is never sent
        self.assertEqual(r.calls[-1]["request_id"], "req_test")

    def test_refusal_or_truncation_yields_no_repair(self):
        client, _ = fake_client(GOOD, stop="refusal")
        r = repair.ClaudeRepair(client=client)
        r.ask = repair.ClaudeRepair.ask.__get__(r)
        try:
            import pymupdf  # noqa: F401
            import anthropic  # noqa: F401
        except ImportError:
            self.skipTest("anthropic / PyMuPDF not installed")
        import pymupdf
        doc = pymupdf.open()
        doc.new_page()
        path = os.path.join(os.path.dirname(__file__), "_tmp_repair2.pdf")
        doc.save(path)
        try:
            self.assertIsNone(r.ask(path, [1], {}, []))
        finally:
            os.remove(path)


class GroqProvider(unittest.TestCase):
    def _groq(self, replies):
        g = repair.GroqRepair()
        sent = []

        def post(body):
            sent.append(body)
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        g._post = post
        return g, sent

    def _ok(self, reply):
        return {"id": "groq_test", "model": "openai/gpt-oss-120b",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 300}}

    def test_strict_schema_first_and_page_text_sent(self):
        g, sent = self._groq([self._ok(GOOD)])
        reply = g.ask("x.pdf", [1], {"lines": []}, ["does not add up"], page_texts=["[page 1]\nTravel | 1 | 250.00"])
        self.assertEqual(reply["total"], 1500.0)
        self.assertEqual(sent[0]["response_format"]["type"], "json_schema")
        self.assertIn("Travel | 1 | 250.00", sent[0]["messages"][1]["content"])
        self.assertNotIn("1500", sent[0]["messages"][1]["content"])          # target-less

    def test_falls_back_to_json_mode_and_rejects_wrong_shape(self):
        import io
        import urllib.error
        bad_request = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"schema not supported"))
        g, sent = self._groq([bad_request, self._ok({"lines": []})])          # missing required fields
        self.assertIsNone(g.ask("x.pdf", [1], {}, [], page_texts=[]))
        self.assertEqual(sent[1]["response_format"]["type"], "json_object")

    def test_rate_limit_waits_as_told_and_retries(self):
        import io
        import urllib.error
        limited = urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO(
            b'{"error":{"message":"Rate limit reached ... Please try again in 2.09s."}}'))
        g, sent = self._groq([limited, self._ok(GOOD)])
        waits = []
        g._post_with_backoff = lambda body, **kw: repair.GroqRepair._post_with_backoff(g, body, sleep=waits.append)
        self.assertEqual(g.ask("x.pdf", [1], {}, [], page_texts=[])["total"], 1500.0)
        self.assertEqual(len(sent), 2)
        self.assertAlmostEqual(waits[0], 3.09, places=2)

    def test_rejected_key_disables_the_provider(self):
        import io
        import urllib.error
        g, _ = self._groq([urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"invalid key"))])
        self.assertIsNone(g.ask("x.pdf", [1], {}, [], page_texts=[]))
        self.assertTrue(g.disabled)
        self.assertIsNone(g.ask("x.pdf", [1], {}, [], page_texts=[]))           # skipped, no request


if __name__ == "__main__":
    unittest.main()
