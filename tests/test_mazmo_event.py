"""Unit tests for the pure parsing of a Mazmo event (date, cover, excerpt)."""

from datetime import UTC, datetime

from app.services.mazmo import RawEventData
from app.services.mazmo_event import excerpt_from_description, parse_event_details


def test_excerpt_takes_first_paragraph_as_plain_text():
    html = "<p>Recuperaremos las ra\u00edces, un Alter para venir a charlar. </p><p>Otro.</p>"

    assert excerpt_from_description(html) == "Recuperaremos las ra\u00edces, un Alter para venir a charlar."


def test_excerpt_decodes_html_entities():
    assert excerpt_from_description("<p>Rock &amp; roll</p>") == "Rock & roll"


def test_excerpt_strips_inline_tags():
    assert excerpt_from_description("<p>Hola <strong>bellos</strong><br>vengan</p>") == "Hola bellos vengan"


def test_excerpt_truncates_at_word_boundary():
    html = "<p>" + " ".join(["palabra"] * 50) + "</p>"
    assert len(html) > 300

    result = excerpt_from_description(html)

    assert result is not None
    assert len(result) <= 283
    assert result.endswith("...")
    assert all(word == "palabra" for word in result.removesuffix("...").split())


def test_excerpt_skips_empty_paragraphs():
    assert excerpt_from_description("<p> </p><p><br></p><p>Texto</p>") == "Texto"


def test_excerpt_returns_none_for_empty_or_missing():
    assert excerpt_from_description(None) is None
    assert excerpt_from_description("") is None
    assert excerpt_from_description("<p></p>") is None


def test_excerpt_without_paragraph_tags_uses_text_up_to_first_newline():
    assert excerpt_from_description("Linea uno\nLinea dos") == "Linea uno"


def test_parse_event_details_reads_date_cover_and_excerpt():
    event: RawEventData = {
        "date": "2026-11-19T23:30:00.000Z",
        "cover": "https://prod.mazmocdn.com/events/covers/wWYdwivkD.jpeg",
        "description": "<p>Hola</p>",
    }

    details = parse_event_details(event)

    assert details.date == datetime(2026, 11, 19, 23, 30, tzinfo=UTC)
    assert details.cover_url == "https://prod.mazmocdn.com/events/covers/wWYdwivkD.jpeg"
    assert details.excerpt == "Hola"


def test_parse_event_details_tolerates_missing_cover():
    event: RawEventData = {"date": "2026-11-19T23:30:00.000Z", "description": "<p>Hola</p>"}

    assert parse_event_details(event).cover_url is None
