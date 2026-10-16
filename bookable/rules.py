"""The rule catalogue.

Every decision the system makes is a small, named rule attached to the function that applies
it. A rule states a general fact about documents or about the ERP — never about a supplier.
`tests/test_rules.py` fails if any registered rule has no test that `@covers` it.

    python -m bookable.rules        # print the catalogue
"""
from __future__ import annotations

RULES: dict[str, tuple[str, str]] = {}      # id -> (statement, where)


def rule(rule_id: str, statement: str):
    """Register the decorated function as the place where `rule_id` is applied."""
    def deco(fn):
        RULES[rule_id] = (statement, f"{fn.__module__}.{fn.__qualname__}")
        return fn
    return deco


def covers(*rule_ids: str):
    """Mark a test as covering one or more rules."""
    def deco(fn):
        fn.covers = rule_ids
        return fn
    return deco


def load_all() -> dict[str, tuple[str, str]]:
    """Import every module that registers rules, and return the catalogue."""
    from . import assemble, llm_classify, masters, repair, solve, understand  # noqa: F401
    from .ocr import readers  # noqa: F401
    from .reading import cells, classify, fields, items, summary  # noqa: F401
    return RULES


if __name__ == "__main__":
    for rid, (statement, where) in sorted(load_all().items()):
        print(f"{rid:38} {statement}\n{'':38} ({where})")
