"""Page readers. Each returns PageLayouts; heavy engines are imported lazily so the
deterministic core (and its tests) never need them.

  text-layer  PyMuPDF words, when the PDF carries real text (exact, free).
  docling     Docling OCR + TableFormer table structure: the primary reader for scans.
  paddle      PaddleOCR: alternative primary reader (words with boxes and confidences).
  got         GOT-OCR 2.0: a second, independent reading for difficult pages. Text only
              (no boxes), so it returns lines in reading order and markdown tables.
"""
from __future__ import annotations

import io
import os
import re

from .layout import PageLayout, Table, Word
from ..rules import rule

DPI = 200


def _fitz():
    import pymupdf as fitz
    return fitz


def page_count(pdf: str) -> int:
    with _fitz().open(pdf) as doc:
        return doc.page_count


def render(pdf: str, page_no: int, dpi: int = DPI):
    """Page image as a PIL image (page_no is 1-based)."""
    from PIL import Image
    with _fitz().open(pdf) as doc:
        pix = doc[page_no - 1].get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


# ── text layer ──────────────────────────────────────────────────────────────

@rule("ocr.visible_text_only", "Only visible glyphs are on the document (no white, transparent or invisible text).")
def _visible_words(page) -> list[Word]:
    """Words built from VISIBLE characters only. A text layer can carry glyphs nobody can see
    (white fill, zero opacity, invisible render mode) — they are not on the document."""
    # each glyph is matched to its own drawing operation by its exact origin point, so a visible
    # glyph sitting next to (or under) an invisible one is never dropped with it
    hidden = {(round(ch[2][0], 1), round(ch[2][1], 1)) for tr in page.get_texttrace()
              if tr["type"] == 3 or tr.get("opacity", 1) == 0
              or (tr.get("color") and min(tr["color"]) >= 0.95)
              for ch in tr["chars"]}

    def invisible(ch) -> bool:
        return (round(ch["origin"][0], 1), round(ch["origin"][1], 1)) in hidden

    words: list[Word] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            cur: list = []
            prev_x1 = None
            for span in line["spans"]:
                gap_limit = 0.25 * span["size"]
                for ch in span["chars"]:
                    if invisible(ch):
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    if ch["c"].isspace() or (prev_x1 is not None and x0 - prev_x1 > gap_limit):
                        if cur:
                            words.append(_word(cur))
                        cur = []
                    if not ch["c"].isspace():
                        cur.append(ch)
                    prev_x1 = x1
            if cur:
                words.append(_word(cur))
    return words


def _word(chars) -> Word:
    return Word("".join(c["c"] for c in chars), min(c["bbox"][0] for c in chars), min(c["bbox"][1] for c in chars),
                max(c["bbox"][2] for c in chars), max(c["bbox"][3] for c in chars))


def text_layer(pdf: str, page_no: int, min_words: int = 25) -> PageLayout | None:
    """The PDF's own (visible) text, if the page has a usable amount of it (not a scan)."""
    with _fitz().open(pdf) as doc:
        page = doc[page_no - 1]
        words = _visible_words(page)
        if len(words) < min_words or not any(re.search(r"\d", w.text) for w in words):
            return None
        tables = []
        try:
            for tb in page.find_tables().tables:
                rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in tb.extract()]
                tables.append(Table(rows, tuple(tb.bbox)))
        except Exception:                         # table finder is best-effort
            pass
        return PageLayout(page_no, page.rect.width, page.rect.height, words, tables, "text-layer")


# ── Docling ─────────────────────────────────────────────────────────────────

_DOCLING = None


def _docling_converter():
    global _DOCLING
    if _DOCLING is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions()
        opts.do_ocr = True
        opts.do_table_structure = True
        opts.table_structure_options.do_cell_matching = False   # scans: trust OCR text, not PDF cells
        if hasattr(opts, "ocr_options") and hasattr(opts.ocr_options, "force_full_page_ocr"):
            opts.ocr_options.force_full_page_ocr = True
        _DOCLING = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _DOCLING


def docling_read(pdf: str) -> list[PageLayout]:
    """Read every page with Docling. Text items become 'words' (phrase boxes); tables keep
    their row/column structure."""
    doc = _docling_converter().convert(pdf).document
    pages: dict[int, PageLayout] = {}
    for no, p in doc.pages.items():
        pages[no] = PageLayout(no, p.size.width, p.size.height, source="docling")

    def box(prov):
        page = pages[prov.page_no]
        bb = prov.bbox.to_top_left_origin(page_height=page.height)
        return page, bb

    for item, _lvl in doc.iterate_items():
        if not getattr(item, "prov", None):
            continue
        page, bb = box(item.prov[0])
        if item.__class__.__name__ == "TableItem":
            grid = item.data.grid
            rows = [[(c.text or "").strip() for c in r] for r in grid]
            page.tables.append(Table(rows, (bb.l, bb.t, bb.r, bb.b)))
            # table cells also become words so line-level parsing sees them
            for r in grid:
                for c in r:
                    if c.text and c.bbox is not None:
                        cb = c.bbox.to_top_left_origin(page_height=page.height)
                        page.words.append(Word(c.text.strip(), cb.l, cb.t, cb.r, cb.b))
        else:
            text = (getattr(item, "text", "") or "").strip()
            if text:
                page.words.append(Word(text, bb.l, bb.t, bb.r, bb.b))
    for no, page in pages.items():
        page.markdown = doc.export_to_markdown(page_no=no) if hasattr(doc, "export_to_markdown") else ""
    return [pages[k] for k in sorted(pages)]


# ── PaddleOCR ───────────────────────────────────────────────────────────────

_PADDLE = None


def _paddle_engine():
    """PaddleOCR when installed; otherwise RapidOCR, which runs the same PP-OCR detection and
    recognition models on PyTorch (no paddlepaddle runtime needed on Windows)."""
    global _PADDLE
    if _PADDLE is None:
        try:
            from paddleocr import PaddleOCR
            eng = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                            use_textline_orientation=False)

            def run(arr):
                for res in eng.predict(arr):
                    yield from zip(res["rec_polys"], res["rec_texts"], res["rec_scores"])
            _PADDLE = ("paddleocr", run)
        except ImportError:
            from rapidocr import EngineType, RapidOCR
            backend = os.environ.get("BOOKABLE_OCR_BACKEND", "")
            if not backend:
                try:
                    import torch  # noqa: F401
                    backend = "torch"
                except ImportError:
                    backend = "onnx"             # CPU-only installs: no PyTorch needed
            if backend == "torch":
                import torch
                eng = RapidOCR(params={"Det.engine_type": EngineType.TORCH, "Cls.engine_type": EngineType.TORCH,
                                       "Rec.engine_type": EngineType.TORCH,
                                       "EngineConfig.torch.use_cuda": torch.cuda.is_available(),
                                       "Global.log_level": "error"})
            else:
                eng = RapidOCR(params={"Global.log_level": "error"})     # ONNX Runtime on CPU

            def run(arr):
                r = eng(arr)
                if r.boxes is not None:
                    yield from zip(r.boxes, r.txts, r.scores)
            _PADDLE = ("pp-ocr", run)
    return _PADDLE


def paddle_read(pdf: str, page_no: int, dpi: int = DPI) -> PageLayout:
    import numpy as np
    name, run = _paddle_engine()
    img = render(pdf, page_no, dpi=dpi)
    scale = 72.0 / dpi
    if dpi != DPI:
        name = f"{name}@{dpi}dpi"
    words = []
    for poly, text, score in run(np.array(img)):
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        words.append(Word(_clean(text), min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale,
                          float(score)))
    return PageLayout(page_no, img.width * scale, img.height * scale, [w for w in words if w.text], [], name)


def _clean(text: str) -> str:
    """NFKC folds full-width punctuation ('：' -> ':') and ligatures OCR engines emit."""
    import unicodedata
    return unicodedata.normalize("NFKC", text or "").strip()


# ── GOT-OCR 2.0 ─────────────────────────────────────────────────────────────

_GOT = None
GOT_MODEL = os.environ.get("BOOKABLE_GOT_MODEL", "stepfun-ai/GOT-OCR-2.0-hf")


def got_read(pdf: str, page_no: int, strips: int = 4) -> PageLayout:
    """Independent second reading with GOT-OCR 2.0. The page is read in overlapping horizontal
    strips (one 1024px view each — whole-page multi-crop does not fit a 4 GB GPU), in plain-text
    mode with a repetition guard (formatted mode degenerates into empty table rows on dense
    pages). GOT returns text without boxes, so each strip line becomes a pseudo-line."""
    global _GOT
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if _GOT is None:
        dtype = torch.float16 if dev == "cuda" else torch.float32
        _GOT = (AutoProcessor.from_pretrained(GOT_MODEL),
                AutoModelForImageTextToText.from_pretrained(GOT_MODEL, torch_dtype=dtype).to(dev).eval())
    proc, model = _GOT
    img = render(pdf, page_no, dpi=200)
    w, h = img.size
    step, overlap = h // strips, 60
    parts = []
    for k in range(strips):
        strip = img.crop((0, max(0, k * step - overlap), w, min(h, (k + 1) * step + overlap)))
        inputs = proc(strip, return_tensors="pt", format=False).to(dev)
        if dev == "cuda":
            inputs = {n: (v.half() if v.is_floating_point() else v) for n, v in inputs.items()}
        with torch.no_grad():
            out = model.generate(**inputs, do_sample=False, tokenizer=proc.tokenizer, stop_strings="<|im_end|>",
                                 max_new_tokens=1200, no_repeat_ngram_size=12, repetition_penalty=1.05)
        parts.append(proc.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True))
    text = "\n".join(parts)
    return layout_from_text(text, page_no, "got")


def layout_from_text(text: str, page_no: int, source: str) -> PageLayout:
    """Turn reading-order text (with markdown / LaTeX tables) into a PageLayout."""
    text = _latex_tables_to_markdown(text)
    words, tables, rows = [], [], []
    y = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append(cells)
            line = "  ".join(c for c in cells if c)
        elif rows:
            tables.append(Table(rows))
            rows = []
        if line:
            x = 0.0
            for tok in line.split():
                words.append(Word(tok, x, y, x + 6 * len(tok), y + 10))
                x += 6 * len(tok) + 6
            y += 14
    if rows:
        tables.append(Table(rows))
    return PageLayout(page_no, 1000, max(y, 1), words, tables, source, text)


def _latex_tables_to_markdown(text: str) -> str:
    def conv(m):
        body = re.sub(r"\\(hline|cline\{[^}]*\})", "", m.group(1))
        rows = [r.strip() for r in body.split("\\\\") if r.strip()]
        return "\n".join("| " + " | ".join(c.strip() for c in r.split("&")) + " |" for r in rows)
    return re.sub(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", conv, text, flags=re.S)
