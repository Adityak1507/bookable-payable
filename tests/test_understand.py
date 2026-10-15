"""Page-reading behaviours, on plain-text pages (2+ spaces separate cells)."""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable import lexicon as lx  # noqa: E402
from bookable.assemble import split  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.understand import Cell, _split_numbers, page_from_text, understand  # noqa: E402
from bookable.rules import covers  # noqa: E402


def read(text):
    return understand(page_from_text(text))


class Cells(unittest.TestCase):
    @covers("cells.merged_amounts")
    def test_merged_amount_and_rate_are_split(self):
        self.assertEqual([c.text for c in _split_numbers(Cell("16,00 EUR 23 %", 0, 100))], ["16,00 EUR", "23 %"])

    @covers("cells.thousands_group")
    def test_thousands_groups_stay_whole(self):
        self.assertEqual([c.text for c in _split_numbers(Cell("63 479.69", 0, 100))], ["63 479.69"])


class Titles(unittest.TestCase):
    @covers("classify.title_not_field_label")
    def test_field_label_is_not_a_title(self):
        page = read("SALES\nQuotation No.   MSI-4055\nItem   Qty   Unit Price   Amount\nWipes   2   14.20   28.40\n"
                    "                    Total   28.40\n")
        self.assertNotEqual(page.doc_type, "ORDER")

    @covers("classify.prominent_title")
    def test_estimate_is_not_an_invoice(self):
        self.assertEqual(read("ESTIMATE\n# EST-1\nItem   Qty   Rate   Amount\nA   1.00   5.00   5.00\n").doc_type, "ORDER")


class Fields(unittest.TestCase):
    def test_month_first_dates_are_proven_by_the_page(self):
        page = read("INVOICE\nDate:   06/05/2018\nDue Date:   06/19/2018\n")
        self.assertEqual((page.invoice_date, page.due_date), ("2018-06-05", "2018-06-19"))

    @covers("fields.label_value")
    def test_invoice_date_label_is_not_an_invoice_number(self):
        page = read("TAX INVOICE   Invoice Date\n25 May 2025\n")
        self.assertEqual(page.invoice_number, "")

    @covers("fields.labelled_tax_ids")
    def test_tax_ids_only_where_labelled(self):
        page = read("ACME Ltd   Dallas TX 75320-3448\nVAT No.: GB447324473\n")
        self.assertEqual(page.vat_ids, ["GB447324473"])


class Amounts(unittest.TestCase):
    @covers("summary.deduction_after_tax", "assemble.provable_gross")
    def test_post_tax_credit_reduces_what_is_owed(self):
        page = read("TAX INVOICE\nDescription   Quantity   Unit Price   VAT   Amount\n"
                    "Checks   133.00   129.00   15%   17,157.00\n"
                    "            Subtotal   17,157.00\n            TOTAL VAT   2,573.55\n"
                    "            TOTAL ZAR   19,730.55\n            Less Amount Credited   13,110.00\n"
                    "            AMOUNT DUE ZAR   6,620.55\n")
        (ir, _), = split([page], "x.pdf")[0]
        self.assertEqual(ir.gross, D("6620.55"))
        self.assertTrue(solve(ir).reconciled)

    @covers("items.rate_column")
    def test_rate_column_without_percent_signs(self):
        page = read("Kood   Nimetus   Kogus   Hind   Summa   KM%\n"
                    "1   Parking A   1   100.00   100.00   24\n"
                    "1   Parking B   1   50.00   50.00   0\n")
        self.assertEqual([li.tax_rate for li in page.lines], [D(24), D(0)])

    @covers("items.headerless_rows")
    def test_rows_that_close_are_items_without_a_header(self):
        page = read("Invoice No:   72\nVan hire Philly to DC   1   $750.00   $750.00*\n"
                    "            Subtotal   $750.00\n            Total   $750.00\n")
        self.assertEqual([(li.quantity, li.total) for li in page.lines], [(D(1), D("750.00"))])

    @covers("summary.label_above")
    def test_stacked_labels_name_the_numbers_below(self):
        page = read("INVOICE\n            SUBTOTAL   TAX   TOTAL(USD)\n            91,580.50   0.00   91,580.50\n")
        self.assertEqual(page.subtotal, D("91580.50"))
        self.assertIn(D("91580.50"), [v for _, v, _ in page.gross_candidates])

    @covers("summary.label_above")
    def test_currency_code_is_not_a_label(self):
        self.assertTrue(lx.has("Kokku Total", lx.GROSS))
        page = read("Valuuta Currency   Kokku Total\nEUR                37767.32\n")
        self.assertIn(D("37767.32"), [v for _, v, _ in page.gross_candidates])


if __name__ == "__main__":
    unittest.main()
