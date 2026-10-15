"""Core translation tests on hand-transcribed documents.

Each IR below is a faithful copy of what the page prints (no ERP decisions). The test
asserts that the solver books the printed gross AND chooses the structure the page has.
Synthetic control cases check that a fix does not fire where the page doesn't prove it.
"""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable.ir import Charge, DocIR, Line, TaxRow  # noqa: E402
from bookable.numbers import detect_decimal_sep, parse_amount  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.rules import covers  # noqa: E402


def L(desc, q, p, total=None, **kw):
    return Line(description=desc, quantity=D(str(q)), unit_price=D(p),
                total=D(total) if total else None, **kw)


class Numbers(unittest.TestCase):
    def test_locales(self):
        self.assertEqual(parse_amount("1.234,56"), D("1234.56"))
        self.assertEqual(parse_amount("1 234,56"), D("1234.56"))
        self.assertEqual(parse_amount("-12 695.94"), D("-12695.94"))
        self.assertEqual(parse_amount("GH₵ 5400.00"), D("5400.00"))
        self.assertEqual(parse_amount("(12.00)"), D("-12.00"))
        self.assertEqual(parse_amount("12.00-"), D("-12.00"))
        self.assertEqual(parse_amount("56,600", decimal_sep=","), D("56.600"))
        self.assertEqual(parse_amount("1,200"), D("1200"))
        self.assertIsNone(parse_amount("N"))

    def test_detect(self):
        self.assertEqual(detect_decimal_sep(["14,76", "590,40", "1.796,54"]), ",")
        self.assertEqual(detect_decimal_sep(["5,400.00", "1049.40"]), ".")


class HandTranscribed(unittest.TestCase):
    @covers("solve.search_structures")
    def test_inv19_compound_levy(self):
        ir = DocIR(currency="GHS", gross=D("8045.40"), subtotal=D("6600.00"), lines=[
            L("Meal per head", 60, "90.00", "5400.00"),
            L("Transportation and Set up", 1, "1200.00", "1200.00")],
            tax_rows=[TaxRow("NHIL", D("2.5"), amount=D("165.00")),
                      TaxRow("GETFund Levy", D("2.5"), amount=D("165.00")),
                      TaxRow("COVID-19 Levy", D("1"), amount=D("66.00")),
                      TaxRow("VAT", D("15"), amount=D("1049.40"))])
        r = solve(ir)
        self.assertTrue(r.reconciled, r.facts)
        self.assertEqual(r.chosen, "header/net")
        self.assertTrue(any(f.kind == "tax_base" and "other levies" in f.message for f in r.facts))

    @covers("solve.search_structures")
    def test_du10_line_vat_footer_is_restatement(self):
        ir = DocIR(doc_type="CREDIT_NOTE", currency="GBP", gross=D("5076.17"), total_tax=D("846.03"), lines=[
            L("Tap Scheduler", 6, "457.24", "2743.44", tax_rate=D("20")),
            L("Tap Cat5e Kit", 2, "743.35", "1486.70", tax_rate=D("20"))],
            tax_rows=[TaxRow("VAT", D("20"), amount=D("846.03"))])
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual(r.chosen, "line/net")
        self.assertEqual(r.payable["invoice_type"], "CREDIT_MEMO")
        self.assertEqual(r.payable["taxes"], [])

    @covers("solve.search_structures")
    def test_inv09_charges_as_lines(self):
        ir = DocIR(currency="EUR", gross=D("37767.32"), total_tax=D("0.00"), lines=[
            L("Import duties", 1, "31889.09", "31889.09", tax_rate=D("0")),
            L("Customs VAT", 1, "5878.23", "5878.23", tax_rate=D("0"))])
        r = solve(ir)
        self.assertTrue(r.reconciled)

    @covers("solve.rounding_policy_b")
    def test_inv13_bucket_rounding_policy_b(self):
        rows = [(63479.69, 24), (-12695.94, 24), (3173.98, 24), (234.86, 24), (13528.54, 0), (8897.68, 0),
                (6976.80, 24), (21380.06, 24), (-4276.01, 24), (1069.00, 24), (875.10, 0), (1939.80, 24),
                (1075.86, 24), (-215.17, 24), (53.79, 24), (119.00, 24), (20711.63, 24), (-2946.89, 24),
                (736.70, 24), (1952.18, 24), (-1108.87, 24), (160.85, 24), (2379.20, 24), (50.00, 24), (12.50, 24)]
        lines = [Line(description="parking", quantity=D(1 if a > 0 else -1), unit_price=D(str(abs(a))),
                      total=D(f"{a:.2f}"), tax_rate=D(t)) for a, t in rows]
        ir = DocIR(currency="EUR", gross=D("152587.46"), total_tax=D("25023.12"), lines=lines,
                   tax_rows=[TaxRow("KM", D("0"), base=D("23301.32"), amount=D("0.00")),
                             TaxRow("KM", D("24"), base=D("104263.02"), amount=D("25023.12"))])
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual(r.chosen, "header/net")
        self.assertEqual(r.payable["invoice_type"], "INVOICE")     # negative lines are not a credit

    @covers("solve.inclusive_proven")
    def test_inv06_inclusive_unprovable_split_books_as_printed(self):
        incl = ["44.99", "24.99", "25.00", "25.00", "25.00", "25.00", "56.99", "39.99", "39.99", "69.99", "72.99",
                "72.99", "69.99", "69.99", "113.99", "125.99", "39.99", "77.98", "57.99", "74.99", "74.99",
                "199.98", "159.99", "39.99", "5.20"]
        ir = DocIR(currency="ZAR", gross=D("1683.98"), subtotal=D("1633.98"), total_tax=D("169.83"),
                   prices_include_tax=True, lines=[L("item", 1, p, p, price_basis="gross") for p in incl],
                   charges=[Charge("Delivery Cost", D("50.00"), "freight")])
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual(r.chosen, "none/net")
        self.assertEqual(r.payable["total_tax_amount"], "169.83")


class Controls(unittest.TestCase):
    @covers("solve.search_structures")
    def test_single_header_vat_stays_at_header(self):
        ir = DocIR(currency="GBP", gross=D("120.00"), lines=[L("a", 1, "60.00", "60.00"), L("b", 2, "20.00", "40.00")],
                   tax_rows=[TaxRow("VAT", D("20"), amount=D("20.00"))])
        r = solve(ir)
        self.assertEqual(r.chosen, "header/net")
        self.assertTrue(all(li["taxes"] == [] for li in r.payable["line_items"]))

    @covers("solve.credit_positive")
    def test_negative_printed_credit(self):
        ir = DocIR(doc_type="CREDIT_NOTE", currency="EUR", gross=D("-400.00"),
                   lines=[L("refund", 1, "-400.00", "-400.00")])
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual(r.payable["line_items"][0]["unit_price"], "400")

    @covers("solve.search_structures")
    def test_withholding_reduces_gross(self):
        ir = DocIR(currency="THB", gross=D("104.00"), lines=[L("fee", 1, "100.00", "100.00")],
                   tax_rows=[TaxRow("VAT", D("7"), amount=D("7.00")),
                             TaxRow("WHT", D("3"), amount=D("3.00"), withholding=True)])
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual(r.payable["taxes"][1]["tax_amount"], "-3.00")

    @covers("solve.price_scale")
    def test_pack_scaling_is_proven_by_line_total(self):
        ir = DocIR(currency="SGD", gross=D("771.66"),
                   lines=[L("screws", 2000, "38.583", "771.66")])
        r = solve(ir)
        self.assertTrue(r.reconciled, r.facts)
        self.assertTrue(any(f.kind == "scale" for f in r.facts))

    @covers("solve.no_plug")
    def test_no_plug_when_unreconcilable(self):
        ir = DocIR(currency="EUR", gross=D("130.00"), lines=[L("a", 1, "100.00", "100.00")],
                   tax_rows=[TaxRow("VAT", D("23"), amount=D("23.00"))])
        r = solve(ir)
        self.assertFalse(r.reconciled)
        self.assertTrue(any(f.kind == "unreconciled" for f in r.facts))
        self.assertEqual(r.payable["line_items"][0]["unit_price"], "100")


if __name__ == "__main__":
    unittest.main()
