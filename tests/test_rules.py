"""Every rule in the catalogue (bookable/rules.py) must be covered by at least one test."""
import glob
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from bookable.rules import load_all  # noqa: E402


class RuleCatalogue(unittest.TestCase):
    def test_every_rule_has_a_test(self):
        covered = set()
        for path in glob.glob(os.path.join(HERE, "test_*.py")):
            with open(path, encoding="utf-8") as fh:
                for ids in re.findall(r"@covers\(([^)]*)\)", fh.read()):
                    covered |= set(re.findall(r'"([^"]+)"', ids))
        rules = load_all()
        missing = sorted(set(rules) - covered)
        self.assertEqual(missing, [], f"rules without a test: {missing}")
        unknown = sorted(covered - set(rules))
        self.assertEqual(unknown, [], f"tests cover unknown rules: {unknown}")

    def test_rules_state_facts_not_documents(self):
        """A rule must not name a document of the open set (no INV-/DU-/HLD- ids in statements)."""
        for rid, (statement, _) in load_all().items():
            self.assertIsNone(re.search(r"\b(INV|DU|HLD)-\d", statement), rid)


if __name__ == "__main__":
    unittest.main()
