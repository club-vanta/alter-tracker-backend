"""
Pure parsing of a Mazmo event: date, cover image and a plain-text excerpt.

No I/O here. MazmoClient.fetch_event_details does the HTTP call and hands the
`event` object of the thread response to parse_event_details.
"""

from datetime import datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from app.services.mazmo import RawEventData

DEFAULT_EXCERPT_MAX_LEN = 280


class MazmoEventDetails(NamedTuple):
    """Event fields we keep from a Mazmo thread (never the address)."""

    date: datetime
    cover_url: str | None
    excerpt: str | None


class _ParagraphCollector(HTMLParser):
    """Collects the text of each <p> (and the text outside any <p>) as separate chunks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.loose_text: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self._current = []
        elif tag == "br":
            self._append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._current is not None:
            self.paragraphs.append("".join(self._current))
            self._current = None

    def handle_data(self, data: str) -> None:
        self._append(data)

    def _append(self, text: str) -> None:
        (self._current if self._current is not None else self.loose_text).append(text)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _truncate_at_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len + 1)
    head = text[:cut] if cut > 0 else text[:max_len]
    return head.rstrip() + "..."


def excerpt_from_description(html: str | None, max_len: int = DEFAULT_EXCERPT_MAX_LEN) -> str | None:
    """
    Plain-text excerpt of a Mazmo event description.

    Takes the first non-empty <p>; without <p> tags, the text up to the first
    line break. Tags are dropped, <br> counts as a space, whitespace collapses
    and the result is cut at a word boundary with "..." past max_len.
    """
    if not html:
        return None

    parser = _ParagraphCollector()
    parser.feed(html)
    parser.close()

    candidates = [_collapse(p) for p in parser.paragraphs]
    if not parser.paragraphs:
        candidates = [_collapse(line) for line in "".join(parser.loose_text).splitlines()]

    first = next((c for c in candidates if c), None)
    return _truncate_at_word(first, max_len) if first else None


def parse_event_details(event: "RawEventData") -> MazmoEventDetails:
    """Read date, cover URL and excerpt from the `event` object of a Mazmo thread."""
    return MazmoEventDetails(
        date=datetime.fromisoformat(event["date"].replace("Z", "+00:00")),
        cover_url=event.get("cover") or None,
        excerpt=excerpt_from_description(event.get("description")),
    )
