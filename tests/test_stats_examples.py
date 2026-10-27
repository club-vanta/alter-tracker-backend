"""The OpenAPI examples of the stats endpoints must stay valid response bodies."""

import pytest
from pydantic import BaseModel

from app.openapi_examples.stats_examples import (
    ARRIVAL_STATS_EXAMPLE,
    ATTENDANCE_STATS_EXAMPLE,
    DEMOGRAPHIC_STATS_EXAMPLE,
    NO_SHOW_STATS_EXAMPLE,
    OPERATIONS_STATS_EXAMPLE,
    RETENTION_STATS_EVENT_EXAMPLE,
    RETENTION_STATS_EXAMPLE,
    WEATHER_STATS_EVENT_EXAMPLE,
    WEATHER_STATS_EXAMPLE,
)
from app.schemas.stats import (
    ArrivalStatsPublic,
    AttendanceStatsPublic,
    DemographicStatsPublic,
    NoShowStatsPublic,
    OperationsStatsPublic,
    RetentionStatsPublic,
    WeatherStatsPublic,
)


@pytest.mark.parametrize(
    ("model", "example"),
    [
        (AttendanceStatsPublic, ATTENDANCE_STATS_EXAMPLE),
        (ArrivalStatsPublic, ARRIVAL_STATS_EXAMPLE),
        (DemographicStatsPublic, DEMOGRAPHIC_STATS_EXAMPLE),
        (NoShowStatsPublic, NO_SHOW_STATS_EXAMPLE),
        (RetentionStatsPublic, RETENTION_STATS_EXAMPLE),
        (RetentionStatsPublic, RETENTION_STATS_EVENT_EXAMPLE),
        (OperationsStatsPublic, OPERATIONS_STATS_EXAMPLE),
        (WeatherStatsPublic, WEATHER_STATS_EXAMPLE),
        (WeatherStatsPublic, WEATHER_STATS_EVENT_EXAMPLE),
    ],
    ids=[
        "attendance",
        "arrivals",
        "demographics",
        "no-shows",
        "retention",
        "retention-event",
        "operations",
        "weather",
        "weather-event",
    ],
)
def test_stats_example_matches_its_response_model(model: type[BaseModel], example: dict):
    model.model_validate(example)


def test_arrival_example_is_consistent():
    example = ARRIVAL_STATS_EXAMPLE
    summary = example["summary"]
    expected_attended = sum(row["signed_up"] - row["cancelled"] - row["no_show"] for row in example["lead_time"])

    assert sum(b["arrivals"] for b in example["curve"]) == summary["arrivals"]
    assert summary["arrivals"] >= expected_attended


def test_no_show_example_is_consistent():
    example = NO_SHOW_STATS_EXAMPLE
    summary = example["summary"]
    totals = (summary["expected"], summary["no_show"])
    lead_time = [(r["signed_up"] - r["cancelled"], r["no_show"]) for r in example["lead_time"]]

    assert summary["no_show_rate"] == round(summary["no_show"] / summary["expected"], 4)
    for groups in (example["meetups"], example["gender"], example["age"], example["history"]):
        assert (sum(g["expected"] for g in groups), sum(g["no_show"] for g in groups)) == totals
        assert all(
            g["no_show_rate"] == (round(g["no_show"] / g["expected"], 4) if g["expected"] else None) for g in groups
        )
    assert (sum(e for e, _ in lead_time), sum(n for _, n in lead_time)) == totals
    assert all(g["expected"] == g["attended"] + g["no_show"] for g in example["guests"])
    assert summary["repeat_guests"] == example["guests_total"] == len(example["guests"])


def test_retention_example_is_consistent():
    example = RETENTION_STATS_EXAMPLE
    summary = example["summary"]
    guests_by_frequency = sum(row["guests"] for row in example["frequency"])

    assert summary["attendees"] == summary["new_guests"] + summary["returning"]
    assert summary["new_share"] == round(summary["new_guests"] / summary["attendees"], 4)
    assert guests_by_frequency == summary["attendees"]
    assert sum(m["new"] for m in example["meetups"]) == summary["new_guests"]
    assert sum(c["size"] for c in example["cohorts"]) == summary["new_guests"]
    assert all(len(c["retention"]) <= 12 for c in example["cohorts"])
    assert len(example["lapsed"]) <= example["lapsed_total"]


def test_retention_event_example_is_consistent():
    example = RETENTION_STATS_EVENT_EXAMPLE
    summary, guests = example["summary"], example["night"]["guests"]
    new = [g for g in guests if g["visit"] == 1]

    assert len(guests) == summary["attendees"]
    assert len(new) == summary["new_guests"]
    assert sum(g["came_back"] for g in new) == summary["came_back"]
    assert [g["visit"] for g in guests] == sorted((g["visit"] for g in guests), reverse=True)
    assert all((g["days_since_previous"] is None) == (g["visit"] == 1) for g in guests)
    backs = [(m["back"], m["back_new"]) for m in example["night"]["afterwards"]]
    assert backs == sorted(backs)
    assert all(back <= sum(g["came_back"] for g in guests) for back, _ in backs)


def test_operations_example_is_consistent():
    example = OPERATIONS_STATS_EXAMPLE
    summary, payments = example["summary"], example["payments"]
    attended = sum(row["attended"] for row in example["guest_types"])

    assert [row["guest_type"] for row in example["guest_types"]] == ["NORMAL", "INVITED", "STAFF", "VENDOR"]
    assert summary["attended"] == attended
    assert all(row["share"] == round(row["attended"] / attended, 4) for row in example["guest_types"])
    assert summary["paid_share"] == payments["paid_share"]
    assert payments["paid_share"] == round(payments["paid"] / (payments["paid"] + payments["pending"]), 4)
    assert payments["paid"] + payments["pending"] + payments["exempt"] <= attended
    assert sum(row["checkins"] for row in example["staff"]) == attended
    assert example["busiest"]["checkins"] >= summary["avg_peak_checkins"]
    assert len(example["ban_list"]) == summary["bans"]


def test_weather_example_is_consistent():
    example = WEATHER_STATS_EXAMPLE
    meetups = example["meetups"]
    bands = ["BELOW_10", "FROM_10_TO_15", "FROM_15_TO_20", "FROM_20_TO_25", "FROM_25"]

    assert [row["band"] for row in example["temperature_bands"]] == bands
    assert [row["band"] for row in example["temperature_bands_before"]] == bands
    assert sum(row["events"] for row in example["temperature_bands"]) == sum(
        1 for m in meetups if m["avg_temperature"] is not None
    )
    assert sum(row["events"] for row in example["temperature_bands_before"]) == sum(
        1 for m in meetups if m["before_avg_temperature"] is not None
    )
    no_row = [m for m in meetups if not m["has_weather"]]
    assert all(m["avg_temperature"] is None and m["before_avg_temperature"] is None for m in no_row)
    assert [m["date"] for m in meetups] == sorted(m["date"] for m in meetups)
