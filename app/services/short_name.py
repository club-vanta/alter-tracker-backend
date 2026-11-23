"""Derive a short public name from a meetup title. Pure function, no I/O."""

import re

_SEPARATORS = (" - ", ": ", ", ")
_KNOWN_PLACES = frozenset({"san telmo", "tan selmo", "microcentro"})
_PREFIX = "alter "
_QUOTED = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')


def _strip_prefix(text: str) -> str:
    text = text.strip()
    if text.lower().startswith(_PREFIX):
        return text[len(_PREFIX) :].strip()
    return text


def _is_place(text: str) -> bool:
    return text.strip().lower() in _KNOWN_PLACES


def _split_first_separator(text: str) -> tuple[str, str]:
    """Split on the separator that appears earliest in the text."""
    found = [(text.find(sep), sep) for sep in _SEPARATORS if sep in text]
    if not found:
        return text, ""
    index, sep = min(found)
    return text[:index], text[index + len(sep) :]


def _join_spaced_letters(name: str) -> str:
    parts = name.split()
    if len(parts) > 1 and all(len(part) == 1 and part.isalpha() for part in parts):
        return "".join(parts).title()
    return name


def short_name(title: str) -> str:
    """Return the short public name for a meetup title."""
    text = title.strip().removesuffix(".").strip()

    quoted = _QUOTED.search(text)
    if quoted:
        name = (quoted.group(1) or quoted.group(2)).strip()
    else:
        head, tail = _split_first_separator(text)
        head = _strip_prefix(head)
        tail = tail.strip()
        name = tail if tail and not _is_place(tail) else head

    name = name or title.strip()
    name = _join_spaced_letters(name)
    return name[:1].upper() + name[1:]
