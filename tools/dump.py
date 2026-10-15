"""Developer tool: show how a document was read and understood.

    python tools/dump.py INV-19 [--lines] [--engine paddle]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from bookable import assemble  # noqa: E402
from bookable.pipeline import Pipeline  # noqa: E402
from bookable.solve import solve  # noqa: E402
from bookable.understand import cells_of  # noqa: E402
from run import _default  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("stem")
ap.add_argument("--lines", action="store_true")
ap.add_argument("--engine", default="paddle")
a = ap.parse_args()

pipe = Pipeline(_default("master_data"), engine=a.engine, second_reader="none")
pdf = os.path.join(_default("documents"), a.stem + ".pdf")
layouts = pipe.read_pages(pdf)
infos = pipe.understand_all(layouts)
for pl, i in zip(layouts, infos):
    print(f"===== page {pl.page_no} [{pl.source}] type={i.doc_type} ({i.title}) no={i.invoice_number} "
          f"date={i.invoice_date} due={i.due_date} cur={i.currency} po={i.po_number} terms={i.payment_terms_text!r}")
    print(f"  supplier={i.supplier.name!r} vat={i.supplier.vat_id} | buyer={i.buyer.name!r}")
    if a.lines:
        for ln in pl.lines():
            print(f"  {ln.y0:6.1f} | " + " | ".join(c.text for c in cells_of(ln)))
    for li in i.lines:
        print(f"  LINE q={li.quantity} p={li.unit_price} t={li.total} d%={li.discount_pct} r={li.tax_rate} "
              f"ta={li.tax_amount} | {li.description[:60]}")
    print(f"  consistency={i.consistency:.2f} subtotal={i.subtotal} total_tax={i.total_tax} incl={i.prices_include_tax}")
    for t in i.tax_rows:
        print(f"  TAX {t.name} rate={t.rate} base={t.base} amt={t.amount} wht={t.withholding}")
    for c in i.charges:
        print(f"  CHARGE {c.bucket} {c.label!r} {c.amount}")
    print(f"  discount={i.header_discount} gross_candidates={i.gross_candidates}")

payables, declined = assemble.split(infos, a.stem + ".pdf")
for ir, notes in payables:
    r = solve(ir)
    print(f"\n>>> payable {ir.invoice_number} pages={ir.pages} gross={ir.gross} reconciled={r.reconciled} "
          f"booked={r.booked} via {r.chosen}")
    for n in notes:
        print("   note:", n)
    for f in r.facts:
        print(f"   fact[{f.kind}]: {f.message}")
    print("   alternatives:", r.alternatives)
for d in declined:
    print(">>> declined", d)
