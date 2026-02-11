"""Lightweight HTML-to-text extraction using Python stdlib."""

import re
from html.parser import HTMLParser

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "tr",
        "br",
        "hr",
        "blockquote",
        "pre",
        "table",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
        "aside",
    }
)


class _HTMLTextExtractor(HTMLParser):
    """Simple HTML parser that extracts visible text."""

    def __init__(self) -> None:
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS and self._skip_depth == 0:
            self._pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        return "".join(self._pieces)


def html_to_text(html: str) -> str:
    """Convert HTML to plain text, stripping tags and normalizing whitespace."""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    text = parser.get_text()
    # Collapse runs of whitespace within lines
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse multiple blank lines into at most two newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
