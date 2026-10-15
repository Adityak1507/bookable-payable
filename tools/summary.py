"""Developer tool: run all documents (OCR cached) and list what does not reconcile."""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
subprocess.run([sys.executable, os.path.join(HERE, "run.py"), "--no-second-reader"], cwd=HERE,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = tot = 0
for f in sorted(glob.glob(os.path.join(HERE, "output", "_audit", "*.json"))):
    a = json.load(open(f, encoding="utf-8"))
    stem = os.path.basename(f)[:-5]
    o = json.load(open(os.path.join(HERE, "output", stem + ".json"), encoding="utf-8"))
    for p in a.get("payables", []):
        tot += 1
        ok += p["reconciled"]
        if not p["reconciled"]:
            fx = [x["message"] for x in p["facts"] if x["kind"] in ("line_mismatch", "delta")][:2]
            print(f"{stem:7} FAIL gross={p['printed_gross']} booked={p['booked']} {' || '.join(fx)[:160]}")
    if not a.get("payables"):
        print(f"{stem:7} no payable; declined={[d['doc_type'] for d in o['declined']]}")
print("reconciled", ok, "/", tot)
