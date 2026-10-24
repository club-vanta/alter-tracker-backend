"""The OpenAPI examples of the stats endpoints must stay valid response bodies."""

import pytest
from pydantic import BaseModel

from app.openapi_examples.stats_examples import (
    ARRIVAL_STATS_EXAMPLE,
    ATTENDANCE_STATS_EXAMPLE,
    DEMOGRAPHIC_STATS_EXAMPLE,
    RETENTION_STATS_EXAMPLE,
)
from app.schemas.stats import ArrivalStatsPublic, AttendanceStatsPublic, DemographicStatsPublic, RetentionStatsPublic


@pytest.mark.parametrize(
    ("model", "example"),
    [
        (AttendanceStatsPublic, ATTENDANCE_STATS_EXAMPLE),
        (ArrivalStatsPublic, ARRIVAL_STATS_EXAMPLE),
        (DemographicStatsPublic, DEMOGRAPHIC_STATS_EXAMPLE),
        (RetentionStatsPublic, RETENTION_STATS_EXAMPLE),
    ],
    ids=["attendance", "arrivals", "demographics", "retention"],
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
