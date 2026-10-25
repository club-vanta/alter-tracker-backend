"""
Stats router - org-scoped statistics over finalized meetups.

GET /organizations/{org_id}/stats/attendance -> attendance per meetup and in total (org admin)
GET /organizations/{org_id}/stats/arrivals   -> arrival times and sign-up lead time (org admin)
GET /organizations/{org_id}/stats/demographics -> gender and age of attendees (org admin)
GET /organizations/{org_id}/stats/retention  -> new and returning guests, cohorts, lapsed regulars (org admin)
GET /organizations/{org_id}/stats/operations -> guest types, payments, check-ins, busiest door slot, bans (org admin)
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_org_admin
from app.models.models import User
from app.openapi_examples.stats_examples import (
    ARRIVAL_STATS_RESPONSES,
    ATTENDANCE_STATS_RESPONSES,
    DEMOGRAPHIC_STATS_RESPONSES,
    OPERATIONS_STATS_RESPONSES,
    RETENTION_STATS_RESPONSES,
)
from app.schemas import (
    ArrivalStatsPublic,
    AttendanceStatsPublic,
    DemographicStatsPublic,
    OperationsStatsPublic,
    RetentionStatsPublic,
)
from app.services.stats.arrivals import arrival_stats
from app.services.stats.attendance import attendance_stats
from app.services.stats.demographics import demographic_stats
from app.services.stats.operations import operations_stats
from app.services.stats.retention import retention_stats
from app.services.stats.scope import InvalidStatsPeriodError, StatsPeriod, is_finalized_meetup_of_org

router = APIRouter(tags=["stats"])


@dataclass(frozen=True)
class StatsScope:
    """The validated period and optional meetup focus shared by every stats endpoint."""

    period: StatsPeriod
    meetup_id: uuid.UUID | None


def stats_scope(
    org_id: uuid.UUID,
    from_date: Annotated[
        date | None, Query(alias="from", description="First local date to include. Omit for no lower bound.")
    ] = None,
    to_date: Annotated[
        date | None, Query(alias="to", description="Local date the period ends before. Omit for no upper bound.")
    ] = None,
    meetup_id: Annotated[
        uuid.UUID | None, Query(description="Focus on one finalized meetup instead of the whole period.")
    ] = None,
    session: Session = Depends(get_session),
    # Declared so FastAPI resolves auth first: 401/403 must win over this dependency's 404/422.
    _admin: User = Depends(get_org_admin),
) -> StatsScope:
    try:
        period = StatsPeriod(from_date, to_date)
    except InvalidStatsPeriodError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Cannot compute stats: 'from' ({from_date}) must be before 'to' ({to_date}). Pick a later 'to' date."
            ),
        ) from None

    if meetup_id is not None and not is_finalized_meetup_of_org(session, org_id, meetup_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Cannot compute stats: meetup {meetup_id} is not a finalized meetup of organization {org_id}. "
                "List the org's meetups to find a finalized one."
            ),
        )

    return StatsScope(period=period, meetup_id=meetup_id)


@router.get(
    "/organizations/{org_id}/stats/attendance",
    response_model=AttendanceStatsPublic,
    summary="Attendance of finalized meetups in a period",
    responses=ATTENDANCE_STATS_RESPONSES,
)
async def get_attendance_stats(
    org_id: uuid.UUID,
    scope: StatsScope = Depends(stats_scope),
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> AttendanceStatsPublic:
    return AttendanceStatsPublic.model_validate(attendance_stats(session, org_id, scope.period, scope.meetup_id))


@router.get(
    "/organizations/{org_id}/stats/arrivals",
    response_model=ArrivalStatsPublic,
    summary="Arrival times and sign-up lead time of finalized meetups",
    responses=ARRIVAL_STATS_RESPONSES,
)
async def get_arrival_stats(
    org_id: uuid.UUID,
    scope: StatsScope = Depends(stats_scope),
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> ArrivalStatsPublic:
    return ArrivalStatsPublic.model_validate(arrival_stats(session, org_id, scope.period, scope.meetup_id))


@router.get(
    "/organizations/{org_id}/stats/demographics",
    response_model=DemographicStatsPublic,
    summary="Gender and age of the people who attend finalized meetups",
    responses=DEMOGRAPHIC_STATS_RESPONSES,
)
async def get_demographic_stats(
    org_id: uuid.UUID,
    scope: StatsScope = Depends(stats_scope),
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> DemographicStatsPublic:
    return DemographicStatsPublic.model_validate(demographic_stats(session, org_id, scope.period, scope.meetup_id))


@router.get(
    "/organizations/{org_id}/stats/retention",
    response_model=RetentionStatsPublic,
    summary="New and returning guests of finalized meetups",
    responses=RETENTION_STATS_RESPONSES,
)
async def get_retention_stats(
    org_id: uuid.UUID,
    scope: StatsScope = Depends(stats_scope),
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> RetentionStatsPublic:
    return RetentionStatsPublic.model_validate(retention_stats(session, org_id, scope.period, scope.meetup_id))


@router.get(
    "/organizations/{org_id}/stats/operations",
    response_model=OperationsStatsPublic,
    summary="Door operations of finalized meetups: guest types, payments, check-ins and bans",
    responses=OPERATIONS_STATS_RESPONSES,
)
async def get_operations_stats(
    org_id: uuid.UUID,
    scope: StatsScope = Depends(stats_scope),
    session: Session = Depends(get_session),
    _admin: User = Depends(get_org_admin),
) -> OperationsStatsPublic:
    return OperationsStatsPublic.model_validate(operations_stats(session, org_id, scope.period, scope.meetup_id))
