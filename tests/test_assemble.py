import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable.assemble import split  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.understand import page_from_text, understand  # noqa: E402
from bookable.rules import covers  # noqa: E402

INVOICE = """INVOICE
Invoice No:  A-1001                     Date:  02.02.2026
Description        Qty     Unit Price     Total
Widget             2       10.00          20.00
Gadget             1       5.00           5.00
                            Subtotal       25.00
                            VAT 20%        5.00
                            Total due      30.00
"""
DELIVERY = """DELIVERY NOTE
Delivery No:  D-77
Description        Qty
Widget             2
"""
DUNNING = """PAYMENT REMINDER
Our invoice A-1001 of 02.02.2026 remains unpaid.
Invoice            Amount
A-1001             30.00
                   Total due      30.00
"""


def pages(*texts):
    return [understand(page_from_text(t, i + 1)) for i, t in enumerate(texts)]


class Grouping(unittest.TestCase):
    @covers("assemble.grouping", "classify.prominent_title")
    def test_invoice_plus_delivery_note(self):
        pays, dec = split(pages(INVOICE, DELIVERY), "x.pdf")
        self.assertEqual(len(pays), 1)
        self.assertEqual([d["doc_type"] for d in dec], ["DELIVERY_NOTE"])
        self.assertTrue(solve(pays[0][0]).reconciled)

    @covers("assemble.second_copy")
    def test_same_invoice_printed_twice_is_one_payable(self):
        pays, dec = split(pages(INVOICE, INVOICE), "x.pdf")
        self.assertEqual(len(pays), 1)
        self.assertEqual(len(pays[0][0].lines), 2)
        self.assertTrue(any("second copy" in n for n in pays[0][1]))

    @covers("assemble.grouping")
    def test_two_invoices_in_one_pdf(self):
        other = INVOICE.replace("A-1001", "A-1002").replace("2       10.00          20.00", "3       10.00          30.00") \
            .replace("25.00", "35.00").replace("5.00\n                            Total due      30.00",
                                                 "7.00\n                            Total due      42.00")
        pays, _ = split(pages(INVOICE, other), "x.pdf")
        self.assertEqual([p[0].invoice_number for p in pays], ["A-1001", "A-1002"])

    @covers("classify.prominent_title")
    def test_dunning_letter_is_declined(self):
        pays, dec = split(pages(DUNNING), "x.pdf")
        self.assertEqual(pays, [])
        self.assertEqual(dec[0]["doc_type"], "DUNNING")


if __name__ == "__main__":
    unittest.main()
