"""Record or compare the system's own results on the real PDFs (a regression baseline).

    python tools/snapshot.py            # compare current behaviour with tests/regression/snapshot.json
    python tools/snapshot.py --update   # accept current behaviour as the new baseline

The snapshot is OUR output (payables, gross, booked, structure, declines) — not an answer key.
It makes any behaviour change on the real documents visible in review.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
SNAPSHOT = os.path.join(HERE, "tests", "regression", "snapshot.json")


def current(documents: str, masters: str) -> dict:
    from bookable.pipeline import Pipeline
    pipe = Pipeline(masters, log=lambda *a: None)
    out = {}
    for name in sorted(os.listdir(documents)):
        if not name.lower().endswith(".pdf"):
            continue
        res, audit = pipe.process(os.path.join(documents, name))
        out[os.path.splitext(name)[0]] = {
            "declined": sorted(d["doc_type"] for d in res["declined"]),
            "payables": [{"invoice_type": p["invoice_type"], "currency": p["currency"], "gross": p["gross_total"],
                          "booked": a["booked"], "reconciled": a["reconciled"], "structure": a["structure"],
                          "lines": len(p["line_items"])}
                         for p, a in zip(res["payables"], audit["payables"])],
        }
    return out


def diff(old: dict, new: dict) -> list[str]:
    msgs = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            msgs.append(f"{k}: {json.dumps(old.get(k))}\n   -> {json.dumps(new.get(k))}")
    return msgs


if __name__ == "__main__":
    from run import _default
    now = current(_default("documents"), _default("master_data"))
    if "--update" in sys.argv or not os.path.exists(SNAPSHOT):
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=1, ensure_ascii=False, sort_keys=True)
        print(f"snapshot written: {len(now)} documents")
    else:
        with open(SNAPSHOT, encoding="utf-8") as fh:
            changes = diff(json.load(fh), now)
        print("\n".join(changes) or "no behaviour change")
