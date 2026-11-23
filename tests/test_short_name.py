"""Tests for the short_name service (public alter names derived from titles)."""

import pytest

from app.services.short_name import short_name


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Alter San Telmo - D I S C R E E T", "Discreet"),
        ("Alter Tan Selmo - Spring Break", "Spring Break"),
        ('Alter Tan Selmo "Secret Face"', "Secret Face"),
        ("Alter Tan Selmo \u201cSecret Face\u201d", "Secret Face"),
        ("Alter a la Vuelta - San Telmo", "A la Vuelta"),
        ("Alter a la Vuelta, San Telmo.", "A la Vuelta"),
        ("Alter a la Vuelta: Full Contact", "Full Contact"),
        ("Alter a la Vuelta: Naci\u00f3n BDSM", "Naci\u00f3n BDSM"),
        ("Alter Tan Selmo: Spring Break", "Spring Break"),
        ("Alter Tan Selmo", "Tan Selmo"),
        ("Open Group, Microcentro", "Open Group"),
        ("  Alter San Telmo  ", "San Telmo"),
    ],
)
def test_short_name_examples(title: str, expected: str):
    """
    Verify short names for real meetup titles.

    WHY: The public site shows these names, so each title shape seen in
    production must map to a stable, readable name.
    """
    assert short_name(title) == expected


def test_short_name_never_empty():
    """
    Verify a bare "Alter" title does not produce an empty name.

    WHY: The public response must always carry a non-empty name.
    """
    assert short_name("Alter") == "Alter"
