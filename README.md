# Bookable Payable

Turns supplier PDFs into autodrafts the ERP (`erp.py`, unchanged) books correctly. See `DESIGN.md`
for how and why.

## Run

```bash
python run.py path/to/documents            # writes output/<X>.json for every <X>.pdf
```

With no argument it reads `./documents` (or `../candidate_kit-2/documents`) and `./master_data`
(or the kit's). Each output file follows the brief's contract: `{"file", "payables": [...], "declined": [...]}`.
Next to it, `output/_audit/<X>.json` explains every decision: which reader read each page, which ERP
structure was chosen and why, the arithmetic facts behind it, and the master-data evidence.

| flag | default | meaning |
|---|---|---|
| `--out DIR` | `output` | where results go |
| `--masters DIR` | `master_data` | master-data folder |
| `--engine paddle\|docling\|got` | `paddle` | primary OCR for scanned pages (pages with a real text layer are read from it) |
| `--second-reader pp-hires\|got\|none` | `pp-hires` | re-read pages whose payable does not reconcile (PP-OCR at 300 dpi, or GOT-OCR 2.0) |
| `--only STEM ...` | all | process only some files |
| `--classifier ollama[:model]` | off | optional local model (via a running Ollama) that names the type of pages declined as OTHER; it never books or changes amounts |

OCR results are cached in `.cache/` (keyed by file hash, engine and page), so re-runs are fast.
`BOOKABLE_OCR_BACKEND=onnx|torch` picks the PP-OCR backend. The default is torch when PyTorch is
installed, otherwise ONNX Runtime on CPU.

## Docker (CPU)

```bash
docker build -t bookable .
docker run --rm -v "$PWD/documents:/data/documents:ro" -v "$PWD/master_data:/data/master_data:ro" \
           -v "$PWD/output:/data/output" bookable
```

The image uses PP-OCR on ONNX Runtime (no GPU, no PyTorch) and downloads its models at build
time, so it runs offline.

## Install without Docker

Use Python 3.12; the OCR packages do not ship wheels for 3.14 yet.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt          # CPU: PP-OCR on ONNX Runtime
# optional: GPU OCR, GOT-OCR 2.0 and Docling (see the comments in the file for the CUDA index)
uv pip install --python .venv -r requirements-gpu.txt
.venv/Scripts/python run.py            # Windows;  .venv/bin/python on Linux/macOS
```

## Test

```bash
python -m unittest discover -s tests   # 81 tests
```

- **Rule tests.** Every decision is one of the rules in `python -m bookable.rules`. Each rule has a test that `@covers` it, and `tests/test_rules.py` fails if one is missing or if a rule statement names a specific document.
- **Regression on the real PDFs.** `tests/test_regression.py` compares the system's results with the recorded snapshot of its *own* results in `tests/regression/snapshot.json` (not an answer key). It runs when the OCR packages are installed.
  - `python tools/snapshot.py` shows any behaviour change.
  - `python tools/snapshot.py --update` accepts the current behaviour as the new baseline.
- **Core needs no OCR.** The core tests need only the standard library; the two OCR-dependent tests skip without it.

## Layout

| path | role |
|---|---|
| `erp.py` | the sealed ERP recompute, byte-identical to the kit's; imported, never modified |
| `bookable/ocr/` | page readers (visible PDF text, PP-OCR, Docling, GOT-OCR 2.0) → one engine-neutral layout |
| `bookable/reading/` | page reading as small rule modules: `cells`, `classify`, `fields`, `items`, `summary` |
| `bookable/understand.py` | runs the reading rules over one page → a faithful transcription |
| `bookable/assemble.py` | pages → documents: continuations, copies, backup pages, currency, the provable gross |
| `bookable/solve.py` | search over ERP structures, verified by `erp_book` (see DESIGN.md) |
| `bookable/masters.py` | indexed master-data resolution; honest blanks |
| `bookable/rules.py` | the rule catalogue |
| `bookable/llm_classify.py` | optional local-model second opinion on the type of declined pages |
| `bookable/lexicon.py` | multilingual accounting vocabulary and date parsing |
| `bookable/pipeline.py` | reading strategy, second reading, audit trail |
| `tools/dump.py STEM` | developer view of how one document was read and solved |
| `tools/summary.py` | run everything and list what does not reconcile |
| `tools/snapshot.py` | record or compare the regression snapshot |
