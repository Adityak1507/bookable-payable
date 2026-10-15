"""Regression on the real PDFs: behaviour must match the recorded snapshot of our own results.

Skipped when the documents, the OCR engine or the snapshot are unavailable (e.g. a bare CI
without OCR models). Accept intended changes with `python tools/snapshot.py --update`.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))


def _ready() -> str:
    try:
        import pymupdf  # noqa: F401
        import rapidocr  # noqa: F401
    except ImportError:
        return "OCR dependencies not installed"
    from run import _default
    if not os.path.isdir(_default("documents")):
        return "documents folder not found"
    if not os.path.exists(os.path.join(HERE, "tests", "regression", "snapshot.json")):
        return "no snapshot recorded"
    return ""


@unittest.skipIf(_ready(), _ready() or "ready")
class RealDocuments(unittest.TestCase):
    def test_behaviour_matches_snapshot(self):
        import snapshot
        from run import _default
        with open(snapshot.SNAPSHOT, encoding="utf-8") as fh:
            old = json.load(fh)
        new = snapshot.current(_default("documents"), _default("master_data"))
        changes = snapshot.diff(old, new)
        self.assertEqual(changes, [], "\n" + "\n".join(changes))


if __name__ == "__main__":
    unittest.main()
