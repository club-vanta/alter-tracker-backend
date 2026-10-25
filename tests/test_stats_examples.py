"""The OpenAPI examples of the stats endpoints must stay valid response bodies."""

import pytest
from pydantic import BaseModel

from app.openapi_examples.stats_examples import (
    ARRIVAL_STATS_EXAMPLE,
    ATTENDANCE_STATS_EXAMPLE,
    DEMOGRAPHIC_STATS_EXAMPLE,
    OPERATIONS_STATS_EXAMPLE,
    RETENTION_STATS_EXAMPLE,
    WEATHER_STATS_EVENT_EXAMPLE,
    WEATHER_STATS_EXAMPLE,
)
from app.schemas.stats import (
    ArrivalStatsPublic,
    AttendanceStatsPublic,
    DemographicStatsPublic,
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
        (RetentionStatsPublic, RETENTION_STATS_EXAMPLE),
        (OperationsStatsPublic, OPERATIONS_STATS_EXAMPLE),
        (WeatherStatsPublic, WEATHER_STATS_EXAMPLE),
        (WeatherStatsPublic, WEATHER_STATS_EVENT_EXAMPLE),
    ],
    ids=["attendance", "arrivals", "demographics", "retention", "operations", "weather", "weather-event"],
)
def test_stats_example_matches_its_response_model(model: type[BaseModel], example: dict):
    model.model_validate(example)


def test_arrival_example_is_consistent():
    example = ARRIVAL_STATS_EXAMPLE
    summary = example["summary"]
    expected_attended = sum(row["signed_up"] - row["cancelled"] - row["no_show"] for row in example["lead_time"])

    assert sum(b["arrivals"] for b in example["curve"]) == summary["arrivals"]
    assert summary["arrivals"] >= expected_attended


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
    grouped = example["rainy"]["events"] + example["dry"]["events"]

    assert [row["band"] for row in example["temperature_bands"]] == [
        "BELOW_10",
        "FROM_10_TO_15",
        "FROM_15_TO_20",
        "FROM_20_TO_25",
        "FROM_25",
    ]
    assert grouped == sum(1 for m in meetups if m["rained"] is not None)
    assert sum(row["events"] for row in example["temperature_bands"]) == sum(
        1 for m in meetups if m["avg_temperature"] is not None
    )
    assert example["without_weather"] == sum(1 for m in meetups if m["avg_temperature"] is None and m["rained"] is None)
    assert [m["date"] for m in meetups] == sorted(m["date"] for m in meetups)
