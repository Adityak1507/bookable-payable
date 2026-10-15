import os
import sys
import unittest
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bookable.ir import DocIR, Party  # noqa: E402
from bookable.masters import Masters  # noqa: E402
from bookable.rules import covers  # noqa: E402

MASTERS = os.environ.get("BOOKABLE_MASTERS") or next(
    (p for p in (os.path.join(ROOT, "master_data"), os.path.join(ROOT, "..", "candidate_kit-2", "master_data"))
     if os.path.isdir(p)), os.path.join(ROOT, "master_data"))


@unittest.skipUnless(os.path.isdir(MASTERS), "master_data folder not found")
class MasterResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = Masters(MASTERS)

    @covers("masters.supplier_keys")
    def test_supplier_by_vat_id(self):
        ir = DocIR(supplier=Party(name="Something else", vat_id="DE 209177122"))
        self.assertEqual(self.m.supplier(ir)[0], "2845695")

    @covers("masters.supplier_keys")
    def test_supplier_by_name_needs_strong_match(self):
        self.assertEqual(self.m.supplier(DocIR(supplier=Party(name="Phocus Direct Communication GmbH")))[0], "2845695")
        self.assertEqual(self.m.supplier(DocIR(supplier=Party(name="Redwater Technologies")))[0], "")
        self.assertEqual(self.m.supplier(DocIR(supplier=Party(name="Phocus Media")))[0], "")

    @covers("masters.tax_country_rate")
    def test_tax_needs_country_when_rate_is_shared(self):
        self.assertEqual(self.m.tax_code("VAT", "15", "")[0], "")          # ZA and GH both 15%
        self.assertEqual(self.m.tax_code("VAT", "15", "GH")[0], "TAX024")
        self.assertEqual(self.m.tax_code("NHIL", "2.5", "GH")[0], "TAX028")
        self.assertEqual(self.m.tax_code("GETFund Levy", "2.5", "GH")[0], "TAX029")
        self.assertEqual(self.m.tax_code("COVID-19 Levy", "1", "GH")[0], "TAX030")
        self.assertEqual(self.m.tax_code("IVA", "13", "PT")[0], "PT_130_IVA")
        self.assertEqual(self.m.tax_code("KM", "24", "EE")[0], "EST_240_VAT")
        self.assertEqual(self.m.tax_code("WHT", "3", "TH")[0], "TH_WHT_003")
        self.assertEqual(self.m.tax_code("VAT", "21", "NL")[0], "")

    @covers("masters.payment_terms")
    def test_payment_terms(self):
        mk = lambda txt, a="", b="": DocIR(payment_terms_text=txt, invoice_date=a, due_date=b)  # noqa: E731
        self.assertEqual(self.m.payment_term(mk("Within 30 days of invoice date"))[0], "Net_30")
        self.assertEqual(self.m.payment_term(mk("30 NET"))[0], "Net_30")
        self.assertEqual(self.m.payment_term(mk("30 päeva"))[0], "Net_30")
        self.assertEqual(self.m.payment_term(mk("", "2026-02-02", "2026-02-12"))[0], "Net_10")

    @covers("masters.buyer_by_address")
    def test_buyer_by_address_fills_only_unique_codes(self):
        ir = DocIR(text="Northwind Services EE OU\nLindenstrasse 15\nEE-10134 TALLINN\n")
        codes, why = self.m.buyer(ir)
        self.assertEqual((codes["company_code"], codes["location_code"]), ("BOLTGROUP", "LOC_EE_001"))
        self.assertEqual(codes["business_unit_code"], "")             # two units share that address
        ir = DocIR(text="Northwind Holdings OU\nLindenstrasse 15\nEE-10134 TALLINN\n")
        self.assertEqual(self.m.buyer(ir)[0]["business_unit_code"], "EE004")
        self.assertEqual(self.m.buyer(DocIR(text="High Street 10\n4300 Durban\n"))[0]["company_code"], "")

    @covers("masters.document_country", "masters.tax_country_rate")
    def test_tax_country_from_document_and_tax_word(self):
        from bookable.masters import country_of
        ir = DocIR(currency="EUR", text="Rua do Ouro 120\n1100-063 Lisboa\nPortugal\n")
        self.assertEqual(country_of(ir), "PT")
        self.assertEqual(country_of(DocIR(currency="USD")), "")         # the US dollar is not national
        self.assertEqual(self.m.tax_code("zzgl. MwSt", "0", "EE")[0], "DE_000_RC")   # the issuer's tax word
        self.assertEqual(self.m.tax_code("GST", "0", "US")[0], "")      # a GST is not a US use tax

    @covers("masters.supplier_is_not_buyer")
    def test_buyer_address_block_is_not_the_supplier(self):
        ir = DocIR(text="Northwind Operations OU\nLindenstrasse 15\n10134 Tallinn\n\n\n\n\n\n\nCloverdale Trading OU\n",
                   supplier=Party(name="Northwind Operations OU", alternatives=["Cloverdale Trading OU"]))
        payable = {"supplier": {"name": "Northwind Operations OU", "vat_id": ""}}
        self.assertTrue(self.m.supplier_not_buyer(ir, payable))
        self.assertEqual(payable["supplier"]["name"], "Cloverdale Trading OU")

    def test_po_exact_only(self):
        self.assertEqual(self.m.po(DocIR(po_number="po-ee-2026-0044"))[0], "PO-EE-2026-0044")
        self.assertEqual(self.m.po(DocIR(po_number="1000021496"))[0], "")


if __name__ == "__main__":
    unittest.main()
