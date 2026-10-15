"""Engine-neutral page layout: words with boxes, grouped into visual lines and tables.

Every reader (PDF text layer, Docling, PaddleOCR, GOT-OCR) converts its output into a
PageLayout, so everything downstream is independent of which engine read the page.
Coordinates are in PDF points with the origin at the top-left.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1e-3)


@dataclass
class TextLine:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def height(self) -> float:
        return sorted(w.h for w in self.words)[len(self.words) // 2]


@dataclass
class Table:
    rows: list[list[str]]                  # cell texts, header row(s) included
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class PageLayout:
    page_no: int                           # 1-based
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    source: str = ""                       # which reader produced it
    markdown: str = ""                     # reader's own reading-order text, if any

    @property
    def mean_conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else 0.0

    def lines(self) -> list[TextLine]:
        return group_lines(self.words)

    def text(self) -> str:
        return "\n".join(l.text for l in self.lines())


def group_lines(words: list[Word]) -> list[TextLine]:
    """Cluster words into visual lines: a word joins a line when its vertical centre lies
    within half a line-height of the line's centre."""
    out: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        for line in reversed(out[-3:]):
            ref = sorted(x.cy for x in line)[len(line) // 2]
            hh = sorted(x.h for x in line)[len(line) // 2]
            if abs(w.cy - ref) <= 0.5 * max(hh, w.h):
                line.append(w)
                break
        else:
            out.append([w])
    return [TextLine(sorted(l, key=lambda w: w.x0)) for l in out]
