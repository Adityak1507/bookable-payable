# Design

## 1. What I understood that I did not on day one

**A document is a calculation, and so is the ERP. The work is translating one into the other.**

On day one an invoice looked like a form: find the fields, copy them out. But every supplier
document states an equation: these lines, taxed on *some* base, plus these charges, minus these
credits, equals what is owed. `erp.py` is a second, fixed equation: per-line nets, per-line taxes,
header taxes on (net − header discount), plus untaxed charges. A faithful copy fails exactly where
the two equations are built differently, and every "special case" in this set is one such mismatch:

| document | its calculation | what a faithful copy books |
|---|---|---|
| INV-19 | VAT 15% is charged on *net + levies* (6,996), not on net | 7,986.00 instead of 8,045.40 |
| DU-10 | VAT per line, then the same VAT restated as a footer | 5,922.20 (tax counted twice) |
| INV-09 | page 2 is the customs declaration restating page 1's duties | 75,534.64 (booked twice) |
| INV-13 | VAT computed once per rate group, the ERP computes per line | one cent over |
| INV-04 | total 19,730.55, minus a credit applied after tax | 19,730.55 instead of 6,620.55 owed |
| INV-25 | prices include IVA, and the invoice is printed twice | 393.60 instead of 176.00 |
| INV-36 | a white, invisible "1" in the text layer next to the visible quantity "1" | quantity 11 |

Seen this way, the failures are not a dozen bugs. Each is the same question asked of a different
document: *which printed number is a component, which is a restatement, and on what base was each
tax computed?* The document itself answers it, through its own arithmetic. That gives the
system's one rule, used at every level: **labels propose, arithmetic disposes.**

- A column is "quantity × price = total" because those numbers multiply out on every row, not because the header says *Kogus* or *Qty*. A column is a tax-rate column because it holds a few small values at one x-position.
- A tax row is recognised because rate × base = amount; a rate-less tax equal to the sum of the rated taxes is their total, not another tax.
- The gross is the printed total that the page's own components add up to (lines + taxes + charges − credits). Among several "Total" labels, arithmetic picks the one the page proves.
- A tax's base is identified by testing which printed base reproduces its printed amount (net, net + other levies, the printed base). That is how INV-19 is solved, and the audit says so: *"VAT 15% is computed on net + other levies, so its printed amount is given explicitly"*.

## 2. Meeting a document unlike any it has seen

The pipeline never asks "which supplier is this?". It runs the same five steps on everything:

1. **Read what is visible.** Use the PDF's text layer when there is one (visible glyphs only), otherwise PP-OCR. A page whose payable does not reconcile is read again (PP-OCR at 300 dpi, or GOT-OCR 2.0), and the reading that reconciles is kept.
2. **Transcribe faithfully** into an intermediate record, with no ERP decision, grouping pages structurally: continuations, second copies, and declined supporting documents with a reason.
3. **Translate by search and verify with `erp_book`** (below).
4. **Flag, never plug.**

Corrections are gated by independent facts (rule 3), which is what protects documents built to
look broken:
- Header VAT moves to the lines only when the lines carry their own rates.
- A summary tax is dropped only when the line taxes reproduce it.
- Net prices are derived from inclusive ones only when the split reproduces a printed tax.
- Printed per-rate amounts replace line-level tax only when line-level tax demonstrably drifts *and* the per-rate table is printed.

Master data is matched on exact keys first (VAT id, IBAN, PO number), then on a bounded,
indexed name search. Taxes are matched by (country, rate, type). Any ambiguity leaves `""`, and
no lookup scans a whole master.

### The solver, stated precisely

- **Search space.** The ERP's model is finite. A payable's structure is a choice of:
  - tax placement: on the line, at the header, or none;
  - tax encoding: as a rate, or as the printed amount;
  - price basis: net, or tax-inclusive with nets derived.

  The charge buckets and lines come from the reading. That gives at most four candidate structures per document (`solve.MODES`), each a complete autodraft built only from values read on the page.
- **Verifier.** Each candidate is booked by the sealed `erp_book`. A candidate is *admissible* if it books the printed gross to the cent. An inclusive derivation must also reproduce a printed tax figure.
- **Objective.** Among admissible candidates, minimise (placement penalty, number of derived values). The document's own placement wins, then the fewest values not printed verbatim. If none is admissible, the most faithful candidate is emitted and flagged, with the delta diagnosed.
- **Deciding signal: arithmetic.** Every reading decision is decided by a printed identity holding: which column, which row, which total, which tax base. That means qty × price = total, rate × base = amount, components = total. Vocabulary only *proposes* candidates.
- **Safety by construction.**
  - Candidate values come only from the transcription, so the printed gross can select a structure but can never change a value.
  - An outer loop re-reads a page with a second reader when the verifier admits no candidate, and keeps the reading that reconciles. Readings are chosen between, never edited.
- **Auditable.** Every decision is one of 65 named rules (`python -m bookable.rules`), each with a test, and every output carries an audit trail (`output/_audit/`).

**Where a model is used.** Only where it adds value and cannot do harm. Optionally (`--classifier ollama`), a small local model names the type of pages the reader declined as OTHER, choosing from a closed list enforced by a JSON schema. It never books a page and never touches an amount, and its answer is recorded in the audit. On the open set it named a form correctly, and it flagged two declined pages as invoices that the arithmetic could not yet read. That is a useful pointer, not a booking.

**Optional repair** (`--repair groq,claude`, off by default). A model re-transcribes only the payables that still do not reconcile. It is never told which total to reach. Its reply is used only if every number in it is printed on the page and `erp_book` then reconciles it. On the four flagged documents nothing was accepted. Groq's text-only model invented a tax total, which the grounding gate rejected, and it could not make INV-37's non-footing page reconcile. That is the gate working as intended.

**Why this generalises.** Every branch in the code is about the ERP's model or about accounting
in general (tax on a base, a credit after tax, a copy of a page). None is about a document. The
vocabulary file holds what "total", "VAT" and "credit note" are called across languages. A new
supplier, language or layout changes the *reading*. When reading fails, the arithmetic says so,
and the result is a flag rather than a wrong booking.

## 3. Documents that cannot be solved the way the others were

**INV-37 (utility reimbursement): the page does not add up.** Its sections are internally
consistent (electrical 1,570.37 + 266.10 + 105.60 = 1,942.07; condenser 393.47 + 23.18 = 416.65).
But the sections, 1,942.07 + 0.00 + 416.65 + 122.00 + 7.02, total **2,487.74**, while the document
says **2,487.73** is owed. No set of printed components can book the printed gross. The only way to
"fix" it is an invented −0.01, which rule 1 forbids. The system emits the faithful record, books
2,487.74, and flags it. Which figure is owed is a question for the supplier, not the parser.

**INV-26 (35-line grocery invoice): a line is missing from the page.** Where row 30 should be
there is only a small "Northwind Consulting SA" text. The 34 printed rows add up to 846.13, but the
printed subtotal and total are 873.53. The missing 27.40 appears nowhere on the document. Any
line that "fills" it would be invented, so the printed rows are booked and the record is flagged.

**INV-06 (tax-inclusive grocery invoice): the tax split is not on the page.** Prices include VAT,
and the VAT is printed once: 169.83 on 1,683.98. That is not 15/115 of the total, so some items
are zero-rated, but the page does not mark which. Any net price derived from it would be a guess.
The system books the inclusive figures as printed (the gross is right), keeps the printed VAT in
`total_tax_amount`, and does not invent per-line rates. That is the honest limit of what the
document supports.

## Current state and known gaps (open set)

- **39 payables found across 42 PDFs; 35 reconcile** to what the document says is owed. Two of those (DU-05, and the untitled Polvex bill in DU-03) print no total, so their gross is the sum of the printed lines, and the audit marks it `gross_inferred`.
- **Declined:** a dunning letter, delivery notes, waybills, a remittance slip, an estimate, and a form.
- **Flagged, none forced:**
  - INV-37 and INV-26: the page itself does not support the stated total (above).
  - HLD-05: excise (IEC) is folded into each line's IVA base, and the amount owed (sub-total with IVA plus a deposit not subject to IVA) is not printed as one figure.
  - HLD-10: a courier's service-type summary table the parser does not yet read as lines.

Each case met on the way became a general rule in the catalogue, not a branch for one document. A test enforces that no rule statement names a document. Examples:
- one currency per payable;
- backup and specification pages add nothing;
- a total that later rows keep adding to is a cascade step, not what is owed;
- a rate column holds rate-like values.

## How this was developed, and what that means for any score

- **Nothing was held back from me.** The system was built and debugged on all 42 PDFs in `documents/`, *including the five HLD-\* files*. They were read, dumped and fixed like the others. A score on them is not a held-back measurement, and the real test is the documents I have not seen. Seven PDFs (INV-31 to INV-37) have no reference answers at all.
- **What I tuned against.** Only self-consistency: does `erp_book` reproduce the total the document prints? A reviewer kit containing reference answers was present in the workspace. I did not use those answers to tune.
- **Where the reference answers were used.** It was scored twice, in memory, at the person's request. That run exposed one false success: HLD-01 had "reconciled" to an intermediate total. I fixed it from the page itself, by generalising the cascade rule above, not from the key. So that rule exists because the key pointed at the document.
- **What guards against fitting.** The rule catalogue (65 rules, each tested on synthetic pages), plus a regression snapshot of the system's own results on the real PDFs (`tools/snapshot.py`). Behaviour changes are therefore visible and reviewed rather than silent.
