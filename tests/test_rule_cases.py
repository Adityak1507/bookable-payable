"""One focused test for each rule not already exercised elsewhere (synthetic pages only)."""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable.assemble import choose_gross, split  # noqa: E402
from bookable.ir import DocIR, Line, TaxRow  # noqa: E402
from bookable.reading import cells, items, summary  # noqa: E402
from bookable.rules import covers  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.understand import Cell, page_from_text, understand  # noqa: E402


def read(text, page_no=1):
    return understand(page_from_text(text, page_no))


def lines_of(text):
    return page_from_text(text).lines()


class CellRules(unittest.TestCase):
    @covers("cells.amount_then_label")
    def test_amount_boxed_with_next_label(self):
        parts = cells._split_number_label(Cell("1.049,60 Sub-Total c/ IVA:", 0, 100))
        self.assertEqual([p.text for p in parts], ["1.049,60", "Sub-Total c/ IVA:"])

    @covers("cells.codes_not_amounts")
    def test_codes_are_not_amounts(self):
        self.assertTrue(cells.is_code("4582314"))
        self.assertTrue(cells.is_code("952-000091"))
        self.assertFalse(cells.is_code("2,743.44"))

    @covers("cells.quantity_forms")
    def test_quantity_forms(self):
        cs = [Cell("4 Std.", 0, 10), Cell("9x", 20, 30), Cell("2/2", 40, 50), Cell("73,00", 60, 70)]
        nums, uom = cells.row_numbers(cs, ",")
        self.assertEqual([v for _, v in nums], [D(4), D(9), D(2), D("73.00")])
        self.assertEqual(uom, "Std.")


class FieldRules(unittest.TestCase):
    @covers("fields.letterhead_supplier")
    def test_letterhead_names_the_supplier(self):
        p = read("Asian Pacific Offices Pty Ltd\nLevel 3, Collins Street\n\n\n\n\n\n\n\n\n\n\n\n"
                 "Remit to:\nZycus Infotech Private Limited\n")
        self.assertEqual(p.supplier.name, "Asian Pacific Offices Pty Ltd")

    @covers("fields.name_not_a_field")
    def test_vendor_number_is_not_a_name(self):
        from bookable.reading.fields import _valid_name
        self.assertEqual(_valid_name("Number 432145"), "")
        self.assertEqual(_valid_name("Your Reference"), "")
        self.assertEqual(_valid_name("Name: Redwater Technologies"), "Redwater Technologies")

    @covers("fields.supplier_id_nearest_name")
    def test_supplier_id_is_the_one_near_its_name(self):
        p = read("Customer: ACME Buyer Ltd\nVAT No.: GB726874417\n\n\n\n\n\n\n\n\n\n\n\n"
                 "Blackpine Consulting Limited   VAT No.: GB447324473\n")
        self.assertEqual(p.supplier.vat_id, "GB447324473")


class Letterhead(unittest.TestCase):
    @covers("fields.prominent_letterhead")
    def test_prominent_top_text_is_the_issuer_not_the_shipper(self):
        from bookable.ocr.layout import PageLayout, Word
        words = [Word("Blueharbor", 60, 10, 160, 32),                       # large letterhead, no legal form
                 Word("CI", 300, 40, 320, 62), Word("(INNVOICE)", 325, 40, 420, 62)]   # damaged title
        y = 120
        for text in ["Due : 16.Jan.2026", "Payment terms: 60 Days Net", "Item Qty Amount",
                     "SHPR: Polvex DISTRIBUTION GMBH", "Total 100.00", "Thank you", "Bank details",
                     "Account 12345", "Swift ABC", "Page 1"]:
            x = 40
            for tok in text.split():
                words.append(Word(tok, x, y, x + 6 * len(tok), y + 10))
                x += 6 * len(tok) + 6
            y += 60
        p = understand(PageLayout(1, 600, 800, words))
        self.assertEqual(p.supplier.name, "Blueharbor")


class CustomerIds(unittest.TestCase):
    @covers("fields.customer_tax_id")
    def test_reverse_charge_id_is_the_buyers(self):
        p = read("Phocus Direct GmbH   USt-IdNr: DE209177122\n\n\n\n\n\n\nVat ID: EE398766194\nReverse Charge\n")
        self.assertEqual(p.supplier.vat_id, "DE209177122")
        self.assertEqual(p.buyer.vat_id, "EE398766194")
        p = read("Your VAT No.: GB726874417\n")
        self.assertEqual((p.supplier.vat_id, p.buyer.vat_id), ("", "GB726874417"))


class ItemRules(unittest.TestCase):
    @covers("items.excl_column_preferred")
    def test_excluding_tax_column_wins(self):
        roles = items._header_roles([Cell("Description", 0, 60), Cell("Price excl.", 100, 160),
                                     Cell("Price incl.", 170, 230), Cell("Amount excl.", 240, 300)])
        self.assertEqual(roles["price"].text, "Price excl.")

    @covers("items.header_describes_items")
    def test_summary_labels_are_not_an_item_header(self):
        self.assertFalse(items._is_header([Cell("TAX", 0, 30), Cell("TOTAL(USD)", 40, 100)]))
        self.assertTrue(items._is_header([Cell("Description", 0, 60), Cell("Qty", 70, 90), Cell("Amount", 100, 150)]))

    @covers("items.inclusive_column")
    def test_inclusive_price_column_marks_lines(self):
        p = read("Item           Unit Price (incl vat)   Total\nBananas   1   44.99   44.99\n")
        self.assertEqual([li.price_basis for li in p.lines], ["gross"])

    @covers("items.price_unit")
    def test_price_per_hundred(self):
        p = read("Pos   Description   Amount   Price   PU   Total\n80   Screws   100 PC   771.66   100   771.66\n")
        li = p.lines[0]
        self.assertEqual((li.quantity, li.unit_price, li.total), (D(100), D("771.66"), D("771.66")))

    @covers("items.row_closes")
    def test_quantity_and_price_are_told_apart(self):
        p = read("Pos   Beschreibung   Einzelpreis   Menge   Gesamtpreis\n1   Projekt   73,00 €   4 Std.   292,00 €\n")
        li = p.lines[0]
        self.assertEqual((li.quantity, li.unit_price, li.total), (D(4), D("73.00"), D("292.00")))

    @covers("items.rows_summing_to_base")
    def test_rows_proven_by_their_sum(self):
        p = read("ARVE INVOICE\n408 PICKUP FEE   24,0%   5,00\n551 DELIVERY COSTS   24,0%   53,40\n"
                 "KM   24,0%   58,40   14,02\n")
        self.assertEqual([li.total for li in p.lines], [D("5.00"), D("53.40")])

    @covers("items.table_ends")
    def test_table_ends_at_totals(self):
        p = read("Item   Qty   Price   Amount\nA   1   10.00   10.00\n            Total   10.00\n"
                 "B   2   5.00   10.00\n")
        self.assertEqual(len(p.lines), 1)

    @covers("items.tax_line_not_item")
    def test_tax_line_is_not_an_item(self):
        self.assertTrue(items._is_tax_line("VAT @16%   9,745.42   60,908.88"))
        p = read("RATE   TAX   NET\nVAT @16%   9,745.42   60,908.88\n")
        self.assertEqual(p.lines, [])

    @covers("items.units_not_amounts")
    def test_weight_is_not_the_amount(self):
        p = read("Item   Description   Net Weight   Shipped Qty   Extended Total\n"
                 "15   ELEC PART   0,004 KG   2 PC   465,45\n")
        self.assertEqual(p.lines[0].total, D("465.45"))

    @covers("items.wrapped_header")
    def test_wrapped_header(self):
        p = read("ITEM   DESCRIPTION   QTY/           EXTENDED\nNO.                  NO. OF USERS   AMOUNT\n"
                 "1   Cloud services   1   91,580.50\n")
        self.assertEqual(p.lines[0].total, D("91580.50"))


class SummaryRules(unittest.TestCase):
    @covers("summary.excl_is_subtotal")
    def test_amount_excl_tax_is_subtotal(self):
        p = read("            Amount Excl Tax   R129,514.32\n            Tax   R19,427.15\n")
        self.assertEqual(p.subtotal, D("129514.32"))
        self.assertEqual([t.amount for t in p.tax_rows], [D("19427.15")])

    @covers("summary.label_left")
    def test_label_left_on_offset_line(self):
        page = page_from_text("Payment by transfer   TOTAL\n")
        page.words.append(type(page.words[0])("EUR 153,58", 300, 7, 360, 17))
        self.assertIn(D("153.58"), [v for _, v, _ in understand(page).gross_candidates])

    @covers("summary.most_specific_label")
    def test_most_specific_label_wins(self):
        self.assertEqual(summary._summary_label("IVA Total:"), "total_tax")
        self.assertEqual(summary._summary_label("Grand Total"), "gross")

    @covers("summary.side_by_side_pairs")
    def test_side_by_side_pairs(self):
        p = read("Desc. Prom.:   1.049,60   Sub-Total c/ IVA:   1.012,14\n")
        self.assertEqual(p.header_discount, D("1049.60"))
        self.assertIn(D("1012.14"), [v for _, v, _ in p.gross_candidates])

    @covers("summary.tax_base_not_tax")
    def test_tax_base_is_not_a_tax(self):
        p = read("            KM alus VAT C.A   0.00\n            Base de incidência   835,27\n")
        self.assertEqual(p.tax_rows, [])


class AssembleRules(unittest.TestCase):
    @covers("assemble.not_gross")
    def test_zero_and_counts_are_not_gross(self):
        gross, _ = choose_gross(D("100.00"), D(0), D(0), D(0), None, None,
                                [(2, D("0.00"), "Sub-total"), (2, D("6"), "Total"), (2, D("100.00"), "Total")])
        self.assertEqual(gross, D("100.00"))

    @covers("assemble.rate_total_restatement")
    def test_rateless_tax_equal_to_rated_is_their_total(self):
        p = read("INVOICE\nItem   Qty   Price   Amount\nA   1   100.00   100.00\n\n\n\n\n"
                 "            24%   100.00   24.00   124.00\n            KM   24.00\n            Total due   124.00\n")
        (ir, notes), = split([p], "x.pdf")[0]
        self.assertEqual([t.rate for t in ir.tax_rows], [D(24)])
        self.assertTrue(solve(ir).reconciled)

    @covers("assemble.remittance_number")
    def test_remittance_slip_supplies_the_number(self):
        inv = "TAX INVOICE\nItem   Qty   Price   Amount\nCleaning   1   100.00   100.00\n            TOTAL ZAR   100.00\n"
        slip = "PAYMENT ADVICE\nInvoice Number   INV-75162461\n            Amount Due   100.00\n"
        pays, declined = split([read(inv, 1), read(slip, 2)], "x.pdf")
        self.assertEqual(pays[0][0].invoice_number, "INV-75162461")
        self.assertEqual(declined[0]["doc_type"], "REMITTANCE")

    @covers("assemble.specification_page")
    def test_specification_page_adds_nothing(self):
        inv = "FAKTURA\nBeskrivelse   Antal   Pris   Belob\nA   1   513,00   513,00\nB   2   86,00   172,00\n" \
              "            I alt DKK   685,00\n"
        spec = "Specifikation\nDato   Beskrivelse   Antal   Pris   Belob\nB   2   86,00   172,00\nA   1   513,00   513,00\n"
        pays, _ = split([read(inv, 1), read(spec, 2)], "x.pdf")
        self.assertEqual(len(pays[0][0].lines), 2)


class SolveRules(unittest.TestCase):
    @covers("solve.keep_printed_quantity")
    def test_printed_quantity_is_kept(self):
        ir = DocIR(currency="EUR", gross=D("465.45"), lines=[Line(description="part", quantity=D(2), total=D("465.45"))])
        r = solve(ir)
        li = r.payable["line_items"][0]
        self.assertEqual(li["quantity"], "2")
        self.assertTrue(r.reconciled)
        self.assertTrue(any(f.kind == "derived" for f in r.facts))


class LocalModel(unittest.TestCase):
    @covers("llm.page_type_second_opinion")
    def test_model_label_is_constrained_and_failure_is_harmless(self):
        import io
        import json as _json
        from unittest import mock
        from bookable.llm_classify import LocalClassifier
        clf = LocalClassifier(url="http://127.0.0.1:9")                  # nothing listens there
        self.assertIsNone(clf.page_type("Supplier registration form"))
        reply = io.BytesIO(_json.dumps({"response": _json.dumps({"label": "FORM"})}).encode())
        with mock.patch("urllib.request.urlopen", return_value=mock.MagicMock(__enter__=lambda s: reply,
                                                                              __exit__=lambda *a: False)):
            self.assertEqual(clf.page_type("Supplier registration form"), "FORM")
        reply = io.BytesIO(_json.dumps({"response": _json.dumps({"label": "PAY_ME"})}).encode())
        with mock.patch("urllib.request.urlopen", return_value=mock.MagicMock(__enter__=lambda s: reply,
                                                                              __exit__=lambda *a: False)):
            self.assertIsNone(clf.page_type("anything"))                   # outside the closed list


class VisibleText(unittest.TestCase):
    @covers("ocr.visible_text_only")
    def test_invisible_glyphs_are_dropped(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        from bookable.ocr.readers import _visible_words
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((100, 100), "QTY 1", color=(0, 0, 0))
        page.insert_text((128.5, 100), "1", color=(1, 1, 1))       # white digit right after the visible one
        words = [w.text for w in _visible_words(page)]
        self.assertIn("1", words)
        self.assertNotIn("11", words)


if __name__ == "__main__":
    unittest.main()
