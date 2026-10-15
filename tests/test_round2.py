"""Behaviours added while working through the flagged documents (all on synthetic pages)."""
import os
import sys
import unittest
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookable.assemble import split  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.understand import page_from_text, understand  # noqa: E402
from bookable.rules import covers  # noqa: E402


def pages(*texts):
    return [understand(page_from_text(t, i + 1)) for i, t in enumerate(texts)]


def one(*texts):
    (pays, declined) = split(pages(*texts), "x.pdf")
    return pays, declined


class TaxTables(unittest.TestCase):
    @covers("summary.rate_table")
    def test_tax_printed_before_its_base(self):
        p = pages("TAX SUMMARY\nRATE   TAX   NET\nVAT @16%   9,745.42   60,908.88\n")[0]
        self.assertEqual([(t.rate, t.base, t.amount) for t in p.tax_rows], [(D(16), D("60908.88"), D("9745.42"))])

    @covers("summary.rate_table")
    def test_rate_with_amount_is_a_tax_row(self):
        p = pages("Summa ilma käibemaksuta   €490,51\nKäibemaks 24%   €117,72\n")[0]
        self.assertTrue(any(t.rate == D(24) and t.amount == D("117.72") for t in p.tax_rows))


class Gross(unittest.TestCase):
    @covers("assemble.unlabelled_gross")
    def test_explicit_amount_due_is_never_overridden_by_an_unlabelled_number(self):
        pays, _ = one("INVOICE\nItem   Qty   Price   Amount\nStorage   1   100.00   100.00\n"
                      "            SUBTOTAL   100.00\n            TOTAL DUE   KES 116.00\n            999.00\n")
        self.assertEqual(pays[0][0].gross, D("116.00"))

    @covers("assemble.inferred_gross")
    def test_blank_total_is_inferred_from_printed_lines_and_marked(self):
        pays, _ = one("INVOICE\nItem   Qty   Price   Amount\nScrews   1   771.66   771.66\nTotal amount in SGD\n")
        ir = pays[0][0]
        self.assertEqual((ir.gross, ir.gross_inferred), (D("771.66"), True))


class Cascade(unittest.TestCase):
    @covers("summary.cascade", "summary.typo_tolerant_labels")
    def test_intermediate_totals_are_not_what_is_owed(self):
        pays, _ = one("INVOICE\nItem   Qty   Unit Price   Amount\nConsulting   10   100.00   1,000.00\n"
                      "            Total   1,000.00\n            SERVICE FEE 10%   100.00\n"
                      "            TOTAL (INCLUDING FEE)   1,100.00\n            Vat 7 %   77.00\n"
                      "            GRAND TOTAL (INCLUDING VAT)   1,177.00\n            3% (WITHOLDING TA)   33.00\n"
                      "            (OTAL PAYMEN)   1,144.00\n")
        ir = pays[0][0]
        self.assertEqual(ir.gross, D("1144.00"))
        r = solve(ir)
        self.assertTrue(r.reconciled)
        self.assertEqual([t["tax_amount"] for t in r.payable["taxes"]], ["77.00", "-33.00"])

    @covers("summary.cascade", "summary.typo_tolerant_labels")
    def test_cascade_proven_by_arithmetic_when_the_last_label_is_damaged(self):
        pays, _ = one("FAKTURA\nBeskrivelse   Antal   Pris   Belob\nLeje   1   3.582,56   3.582,56\n\n\n\n\n"
                      "            I alt DKK   3.582,56\n            25% moms   895,64\n"
                      "            1 alt DKK inkl. moms   4.478,20\n")
        self.assertEqual(pays[0][0].gross, D("4478.20"))

    @covers("summary.component_total")
    def test_a_total_naming_a_component_is_not_the_document_total(self):
        p = pages("            Total Valor IEC   88,33\n            Total a pagar   1.012,14\n")[0]
        self.assertEqual([v for _, v, _ in p.gross_candidates], [D("1012.14")])


class Pages(unittest.TestCase):
    @covers("assemble.single_currency")
    def test_pages_in_another_currency_restate_the_payable(self):
        eur = "INVOICE\nInvoice Number   5547\nItem   Qty   Price   Amount\nPart   2   100.00   200.00\n            TOTAL:   200.00 EUR\n"
        try_ = "INVOICE\nInvoice Number   5547\nItem   Qty   Price   Amount\nPart   2   3500.00   7000.00\n            TOTAL:   7000.00 TRY\n"
        pays, _ = one(eur, try_)
        self.assertEqual(len(pays), 1)
        self.assertEqual((pays[0][0].currency, pays[0][0].gross), ("EUR", D("200.00")))
        self.assertTrue(solve(pays[0][0]).reconciled)

    @covers("assemble.backup_pages")
    def test_untitled_backup_after_a_self_sufficient_invoice_adds_nothing(self):
        inv = "INVOICE\nInvoice No:   39015\nDescription   Qty   Price   Amount\nElectric   1   500.00   500.00\n" \
              "            Amount Due:   500.00\n"
        backup = "Utility statement\nDescription   Qty   Price   Amount\nkWh   1000   0.40   400.00\nDelivery   1   100.00   100.00\n" \
                 "            Total Current Charges   500.00\n"
        pays, _ = one(inv, backup)
        ir = pays[0][0]
        self.assertEqual([li.total for li in ir.lines], [D("500.00")])
        self.assertTrue(solve(ir).reconciled)


class Rows(unittest.TestCase):
    @covers("items.set_component")
    def test_set_components_priced_but_not_billed(self):
        p = pages("Arve #1\nToode            Hind      Kokku käibemaksuta\n1x Speaker set   €103,60   €103,60\n"
                  "2x Speaker sub   €20,00\n")[0]
        self.assertEqual([li.total for li in p.lines], [D("103.60")])

    @covers("items.discount_value")
    def test_discount_printed_as_percent_and_value(self):
        p = pages("Qtd   Preço   Ilíquido   Desc %   Valor\nTRINCA ALE   1   123,36   123,36   51,76 %   63,85\n")[0]
        li = p.lines[0]
        self.assertEqual((li.unit_price, li.discount_pct, li.total), (D("123.36"), D("51.76"), D("59.51")))

    @covers("items.rate_column")
    def test_money_amounts_are_not_tax_rates(self):
        p = pages("Item   Qty   Price   Amount   IEC   IVA %\nBeer   1   48.00   48.00   2.68   23\n"
                  "Beer B   4   18.72   74.88   4.36   23\n")[0]
        self.assertEqual([li.tax_rate for li in p.lines], [D(23), D(23)])


if __name__ == "__main__":
    unittest.main()
