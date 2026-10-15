"""Run the system over a folder of PDFs.

    python run.py [DOCUMENTS_DIR] [--out output] [--masters MASTER_DIR] [--engine docling|paddle|got]
                  [--second-reader pp-hires|got|none] [--only NAME ...]

Writes output/<X>.json for every <X>.pdf, and output/_audit/<X>.json explaining each decision
(which reader, which structure, why that gross, which facts, master-data evidence).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bookable.pipeline import Pipeline  # noqa: E402

KIT = os.path.join(os.path.dirname(HERE), "candidate_kit-2")


def _default(name: str) -> str:
    local = os.path.join(HERE, name)
    return local if os.path.isdir(local) or not os.path.isdir(os.path.join(KIT, name)) else os.path.join(KIT, name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("documents", nargs="?", default=_default("documents"))
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    ap.add_argument("--masters", default=_default("master_data"))
    ap.add_argument("--engine", default="paddle", choices=["docling", "paddle", "got"])
    ap.add_argument("--second-reader", default="pp-hires", choices=["pp-hires", "got", "none"],
                    help="re-read pages whose payable does not reconcile (default: PP-OCR at 300 dpi)")
    ap.add_argument("--no-second-reader", action="store_true", help="same as --second-reader none")
    ap.add_argument("--only", nargs="*", help="process only these file stems")
    ap.add_argument("--classifier", default="", help="optional local model for pages declined as OTHER, "
                    "e.g. ollama or ollama:qwen2.5-coder:7b (needs a running Ollama server)")
    a = ap.parse_args()

    pdfs = sorted(p for p in glob.glob(os.path.join(a.documents, "*")) if p.lower().endswith(".pdf"))
    if a.only:
        pdfs = [p for p in pdfs if os.path.splitext(os.path.basename(p))[0] in set(a.only)]
    if not pdfs:
        print(f"no PDFs in {a.documents}", file=sys.stderr)
        return 1
    os.makedirs(os.path.join(a.out, "_audit"), exist_ok=True)
    clf = None
    if a.classifier.startswith("ollama"):
        from bookable.llm_classify import LocalClassifier
        model = a.classifier.split(":", 1)[1] if ":" in a.classifier else "qwen2.5-coder:7b"
        clf = LocalClassifier(model)
    pipe = Pipeline(a.masters, engine=a.engine,
                    second_reader="none" if a.no_second_reader else a.second_reader, classifier=clf)

    summary = []
    for pdf in pdfs:
        stem = os.path.splitext(os.path.basename(pdf))[0]
        t0 = time.time()
        try:
            out, audit = pipe.process(pdf)
        except Exception as e:                    # one bad file must not stop the batch
            out = {"file": os.path.basename(pdf), "payables": [],
                   "declined": [{"doc_type": "UNREADABLE", "reason": f"could not read document: {e}"}]}
            audit = {"file": out["file"], "error": repr(e)}
        with open(os.path.join(a.out, f"{stem}.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
        with open(os.path.join(a.out, "_audit", f"{stem}.json"), "w", encoding="utf-8") as fh:
            json.dump(audit, fh, indent=2, ensure_ascii=False, default=str)
        rec = [p.get("reconciled") for p in audit.get("payables", [])]
        line = (f"{stem:8} payables={len(out['payables'])} declined={len(out['declined'])} "
                f"reconciled={sum(bool(r) for r in rec)}/{len(rec)}  ({time.time() - t0:.1f}s)")
        print(line, flush=True)
        summary.append(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
