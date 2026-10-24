"""The OpenAPI examples of the stats endpoints must stay valid response bodies."""

import pytest
from pydantic import BaseModel

from app.openapi_examples.stats_examples import (
    ARRIVAL_STATS_EXAMPLE,
    ATTENDANCE_STATS_EXAMPLE,
    DEMOGRAPHIC_STATS_EXAMPLE,
)
from app.schemas.stats import ArrivalStatsPublic, AttendanceStatsPublic, DemographicStatsPublic


@pytest.mark.parametrize(
    ("model", "example"),
    [
        (AttendanceStatsPublic, ATTENDANCE_STATS_EXAMPLE),
        (ArrivalStatsPublic, ARRIVAL_STATS_EXAMPLE),
        (DemographicStatsPublic, DEMOGRAPHIC_STATS_EXAMPLE),
    ],
    ids=["attendance", "arrivals", "demographics"],
)
def test_stats_example_matches_its_response_model(model: type[BaseModel], example: dict):
    model.model_validate(example)


def test_arrival_example_is_consistent():
    example = ARRIVAL_STATS_EXAMPLE
    summary = example["summary"]
    expected_attended = sum(row["signed_up"] - row["cancelled"] - row["no_show"] for row in example["lead_time"])

    assert sum(b["arrivals"] for b in example["curve"]) == summary["arrivals"]
    assert summary["arrivals"] >= expected_attended
