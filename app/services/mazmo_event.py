"""
Pure parsing of a Mazmo event: date, cover image and a plain-text excerpt.

No I/O here. MazmoClient.fetch_event_details does the HTTP call and hands the
`event` object of the thread response to parse_event_details.
"""

from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from typing import NamedTuple

import structlog
from pydantic import BaseModel, field_validator

log = structlog.get_logger(__name__)

DEFAULT_EXCERPT_MAX_LEN = 280
COVER_URL_MAX_LEN = 512  # matches the meetup.cover_url column
_BLOCK_TAGS = frozenset({"br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"})


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
        elif tag in _BLOCK_TAGS:
            self._append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._current is not None:
            self.paragraphs.append("".join(self._current))
            self._current = None
        elif tag in _BLOCK_TAGS:
            self._append(" ")

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
    line break. Tags are dropped, <br>, <div>, <li> and headings count as a
    space, whitespace collapses
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


class _MazmoEvent(BaseModel):
    """The `event` object of a Mazmo thread, validated. Unknown fields are ignored."""

    date: datetime
    cover: str | None = None
    description: str | None = None

    @field_validator("cover", mode="before")
    @classmethod
    def _drop_invalid_cover(cls, value: object) -> str | None:
        """
        An unusable cover is dropped to None instead of failing the event.

        The date is what create_meetup needs; a missing cover only means the
        public site shows no image. Anything that is not an https URL of at
        most COVER_URL_MAX_LEN characters would break the DB column or the site.
        """
        if value is None or value == "":
            return None
        if isinstance(value, str) and value.startswith("https://") and len(value) <= COVER_URL_MAX_LEN:
            return value
        log.warning("Ignoring invalid Mazmo event cover", cover_type=type(value).__name__)
        return None


def parse_event_details(event: Mapping[str, object]) -> MazmoEventDetails:
    """
    Read date, cover URL and excerpt from the `event` object of a Mazmo thread.

    Raises:
        pydantic.ValidationError: If `event` is not an object or has no valid date
            (or a description that is not a string). An invalid cover is dropped, not raised.
    """
    parsed = _MazmoEvent.model_validate(event)
    return MazmoEventDetails(
        date=parsed.date,
        cover_url=parsed.cover,
        excerpt=excerpt_from_description(parsed.description),
    )
