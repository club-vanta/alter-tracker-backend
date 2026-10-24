"""
Stats router - org-scoped statistics over finalized meetups.

GET /organizations/{org_id}/stats/attendance -> attendance per meetup and in total (org admin)
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.deps import get_org_admin
from app.models.models import User
from app.openapi_examples.stats_examples import ATTENDANCE_STATS_RESPONSES
from app.schemas import AttendanceStatsPublic
from app.services.stats.attendance import attendance_stats
from app.services.stats.scope import InvalidStatsPeriodError, StatsPeriod, is_finalized_meetup_of_org

router = APIRouter(tags=["stats"])


@router.get(
    "/organizations/{org_id}/stats/attendance",
    response_model=AttendanceStatsPublic,
    summary="Attendance of finalized meetups in a period",
    responses=ATTENDANCE_STATS_RESPONSES,
)
async def get_attendance_stats(
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
    _admin: User = Depends(get_org_admin),
) -> AttendanceStatsPublic:
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

    return AttendanceStatsPublic.model_validate(attendance_stats(session, org_id, period, meetup_id))
