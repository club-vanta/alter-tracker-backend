"""
OpenAPI examples for stats router endpoints.

Endpoints:
  GET /organizations/{org_id}/stats/attendance
  GET /organizations/{org_id}/stats/arrivals
  GET /organizations/{org_id}/stats/demographics
  GET /organizations/{org_id}/stats/retention
  GET /organizations/{org_id}/stats/operations
  GET /organizations/{org_id}/stats/weather
"""

from typing import Any

from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_admin_required,
    error_404_meetup,
    error_422_validation,
    merge_error_responses,
)

# -- GET /organizations/{org_id}/stats/attendance --

ATTENDANCE_STATS_EXAMPLE = {
    "meetups": [
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "Alter Abril",
            "date": "2026-04-05T22:00:00Z",
            "signed_up": 40,
            "cancelled": 4,
            "attended": 30,
            "walkin": 3,
            "no_show": 9,
        },
        {
            "meetup_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "name": "Alter Abril II",
            "date": "2026-04-19T22:00:00Z",
            "signed_up": 36,
            "cancelled": 2,
            "attended": 31,
            "walkin": 1,
            "no_show": 4,
        },
    ],
    "totals": {
        "events": 2,
        "signed_up": 76,
        "cancelled": 6,
        "attended": 61,
        "walkin": 4,
        "no_show": 13,
        "cancellation_rate": 0.0789,
        "no_show_rate": 0.1857,
        "avg_signed_up_per_event": 38.0,
        "avg_attended_per_event": 30.5,
    },
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {
        "events": 2,
        "signed_up": 70,
        "cancelled": 7,
        "attended": 54,
        "walkin": 2,
        "no_show": 11,
        "cancellation_rate": 0.1,
        "no_show_rate": 0.1746,
        "avg_signed_up_per_event": 35.0,
        "avg_attended_per_event": 27.0,
    },
}

ATTENDANCE_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Attendance of finalized meetups in the period",
            "content": {
                "application/json": {
                    "examples": {
                        "attendance": {
                            "summary": "Two meetups compared with the previous period",
                            "value": ATTENDANCE_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)

# -- GET /organizations/{org_id}/stats/arrivals --

ARRIVAL_STATS_EXAMPLE = {
    "curve": [
        {"start_minute": -15, "local_time": None, "arrivals": 3},
        {"start_minute": 0, "local_time": None, "arrivals": 10},
        {"start_minute": 15, "local_time": None, "arrivals": 17},
        {"start_minute": 30, "local_time": None, "arrivals": 15},
        {"start_minute": 45, "local_time": None, "arrivals": 11},
        {"start_minute": 60, "local_time": None, "arrivals": 6},
        {"start_minute": 75, "local_time": None, "arrivals": 3},
        {"start_minute": 90, "local_time": None, "arrivals": 1},
    ],
    "summary": {
        "arrivals": 66,
        "excluded_arrivals": 2,
        "peak": {"start_minute": 15, "local_time": None, "arrivals": 17},
        "peak_local_time": "22:15",
        "mean_minutes": 34,
        "p90_minutes": 66,
        "mean_local_time": "22:34",
        "p90_local_time": "23:06",
        "first_hour_share": 0.8485,
    },
    "lead_time": [
        {"bucket": "SAME_DAY", "signed_up": 12, "cancelled": 1, "no_show": 2, "no_show_rate": 0.1818},
        {"bucket": "ONE_DAY", "signed_up": 20, "cancelled": 2, "no_show": 3, "no_show_rate": 0.1667},
        {"bucket": "TWO_THREE", "signed_up": 25, "cancelled": 3, "no_show": 4, "no_show_rate": 0.1818},
        {"bucket": "FOUR_SEVEN", "signed_up": 14, "cancelled": 2, "no_show": 2, "no_show_rate": 0.1667},
        {"bucket": "EIGHT_FOURTEEN", "signed_up": 8, "cancelled": 1, "no_show": 1, "no_show_rate": 0.1429},
        {"bucket": "FIFTEEN_PLUS", "signed_up": 4, "cancelled": 1, "no_show": 0, "no_show_rate": 0.0},
    ],
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {
        "arrivals": 44,
        "excluded_arrivals": 1,
        "peak": {"start_minute": 15, "local_time": None, "arrivals": 12},
        "peak_local_time": "22:15",
        "mean_minutes": 36,
        "p90_minutes": 66,
        "mean_local_time": "22:36",
        "p90_local_time": "23:06",
        "first_hour_share": 0.8409,
    },
}

ARRIVAL_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Arrival times and sign-up lead time of finalized meetups in the period",
            "content": {
                "application/json": {
                    "examples": {
                        "arrivals": {
                            "summary": "A period compared with the previous period",
                            "value": ARRIVAL_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)


# -- GET /organizations/{org_id}/stats/demographics --

DEMOGRAPHIC_STATS_EXAMPLE = {
    "gender": [
        {
            "key": "MALE",
            "attended": 36,
            "expected": 42,
            "no_show": 7,
            "no_show_rate": 0.1667,
            "attended_share": 0.45,
            "expected_share": 0.4468,
        },
        {
            "key": "WOMAN_CIS",
            "attended": 28,
            "expected": 33,
            "no_show": 6,
            "no_show_rate": 0.1818,
            "attended_share": 0.35,
            "expected_share": 0.3511,
        },
        {
            "key": "UNDEFINED",
            "attended": 6,
            "expected": 7,
            "no_show": 1,
            "no_show_rate": 0.1429,
            "attended_share": 0.075,
            "expected_share": 0.0745,
        },
        {
            "key": None,
            "attended": 10,
            "expected": 12,
            "no_show": 2,
            "no_show_rate": 0.1667,
            "attended_share": 0.125,
            "expected_share": 0.1277,
        },
    ],
    "age": [
        {
            "key": "AGE_18_24",
            "attended": 16,
            "expected": 20,
            "no_show": 4,
            "no_show_rate": 0.2,
            "attended_share": 0.2,
            "expected_share": 0.2128,
        },
        {
            "key": "AGE_25_29",
            "attended": 20,
            "expected": 23,
            "no_show": 4,
            "no_show_rate": 0.1739,
            "attended_share": 0.25,
            "expected_share": 0.2447,
        },
        {
            "key": "AGE_30_34",
            "attended": 14,
            "expected": 16,
            "no_show": 3,
            "no_show_rate": 0.1875,
            "attended_share": 0.175,
            "expected_share": 0.1702,
        },
        {
            "key": "AGE_35_39",
            "attended": 8,
            "expected": 9,
            "no_show": 1,
            "no_show_rate": 0.1111,
            "attended_share": 0.1,
            "expected_share": 0.0957,
        },
        {
            "key": "AGE_40_49",
            "attended": 8,
            "expected": 9,
            "no_show": 1,
            "no_show_rate": 0.1111,
            "attended_share": 0.1,
            "expected_share": 0.0957,
        },
        {
            "key": "AGE_50_PLUS",
            "attended": 4,
            "expected": 5,
            "no_show": 1,
            "no_show_rate": 0.2,
            "attended_share": 0.05,
            "expected_share": 0.0532,
        },
        {
            "key": "UNKNOWN",
            "attended": 10,
            "expected": 12,
            "no_show": 2,
            "no_show_rate": 0.1667,
            "attended_share": 0.125,
            "expected_share": 0.1277,
        },
    ],
    "summary": {
        "attended": 80,
        "median_age": 28.5,
        "known_age_share": 0.875,
    },
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {
        "attended": 72,
        "median_age": 29.0,
        "known_age_share": 0.8611,
    },
}

DEMOGRAPHIC_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Gender and age of the people who attended finalized meetups in the period",
            "content": {
                "application/json": {
                    "examples": {
                        "demographics": {
                            "summary": "A period compared with the previous period",
                            "value": DEMOGRAPHIC_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)


# -- GET /organizations/{org_id}/stats/retention --

# Six monthly meetups, 2026-01-01 to 2026-07-01, 40 unique guests with 88 attendances
# between them. 16 had their first time in the period (the cohort sizes and the "new"
# column), 24 had come before. The cohort lists run to July because "today" is in July.
RETENTION_STATS_EXAMPLE = {
    "summary": {
        "attendees": 40,
        "new_guests": 16,
        "returning": 24,
        "new_share": 0.4,
        "first_timer_return_rate": 0.625,
    },
    "meetups": [
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567801",
            "name": "Alter Enero",
            "date": "2026-01-10T22:00:00Z",
            "new": 5,
            "returning": 8,
        },
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567802",
            "name": "Alter Febrero",
            "date": "2026-02-07T22:00:00Z",
            "new": 3,
            "returning": 11,
        },
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567803",
            "name": "Alter Marzo",
            "date": "2026-03-07T22:00:00Z",
            "new": 3,
            "returning": 12,
        },
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567804",
            "name": "Alter Abril",
            "date": "2026-04-04T22:00:00Z",
            "new": 2,
            "returning": 13,
        },
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567805",
            "name": "Alter Mayo",
            "date": "2026-05-09T22:00:00Z",
            "new": 2,
            "returning": 14,
        },
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567806",
            "name": "Alter Junio",
            "date": "2026-06-06T22:00:00Z",
            "new": 1,
            "returning": 14,
        },
    ],
    "cohorts": [
        {"month": "2026-01", "size": 5, "retention": [0.6, 0.4, 0.4, 0.2, 0.4, 0.2]},
        {"month": "2026-02", "size": 3, "retention": [0.6667, 0.3333, 0.3333, 0.0, 0.3333]},
        {"month": "2026-03", "size": 3, "retention": [0.6667, 0.3333, 0.6667, 0.3333]},
        {"month": "2026-04", "size": 2, "retention": [0.5, 0.5, 0.0]},
        {"month": "2026-05", "size": 2, "retention": [0.5, 0.5]},
        {"month": "2026-06", "size": 1, "retention": [1.0]},
    ],
    "frequency": [
        {"bucket": "ONE", "guests": 16},
        {"bucket": "TWO", "guests": 12},
        {"bucket": "THREE_FIVE", "guests": 10},
        {"bucket": "SIX_PLUS", "guests": 2},
    ],
    "lapsed": [
        {
            "guest_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "displayname": "Lucia",
            "attended_before": 8,
            "last_attended": "2025-12-13T22:00:00Z",
        },
        {
            "guest_id": "b2c3d4e5-f6a7-8901-bcde-f12345678902",
            "displayname": "Mateo",
            "attended_before": 3,
            "last_attended": "2025-12-13T22:00:00Z",
        },
    ],
    "lapsed_total": 7,
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {
        "attendees": 34,
        "new_guests": 12,
        "returning": 22,
        "new_share": 0.3529,
        "first_timer_return_rate": 0.5,
    },
}

RETENTION_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "New and returning guests, cohorts, frequency and lapsed regulars of finalized meetups",
            "content": {
                "application/json": {
                    "examples": {
                        "retention": {
                            "summary": "A period compared with the previous period",
                            "value": RETENTION_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)


# -- GET /organizations/{org_id}/stats/operations --

# Two meetups, one of them paying. 60 people came in, counting every guest type; the
# paying meetup had 30 attended, of whom 20 paid, 6 owe and 4 are exempt.
OPERATIONS_STATS_EXAMPLE = {
    "summary": {"attended": 60, "paid_share": 0.7692, "avg_peak_checkins": 14.5, "bans": 2},
    "guest_types": [
        {"guest_type": "NORMAL", "attended": 48, "share": 0.8},
        {"guest_type": "INVITED", "attended": 6, "share": 0.1},
        {"guest_type": "STAFF", "attended": 4, "share": 0.0667},
        {"guest_type": "VENDOR", "attended": 2, "share": 0.0333},
    ],
    "payments": {
        "paying_events": 1,
        "paid": 20,
        "pending": 6,
        "exempt": 4,
        "cancelled_but_paid": 1,
        "paid_share": 0.7692,
    },
    "staff": [
        {"user_id": 12, "username": "carlos", "checkins": 34},
        {"user_id": 15, "username": "sofia", "checkins": 20},
        {"user_id": None, "username": None, "checkins": 6},
    ],
    "busiest": {
        "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567801",
        "meetup_name": "Alter Abril",
        "local_time": "20:15",
        "checkins": 17,
    },
    "ban_list": [
        {
            "guest_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "displayname": "Lucia",
            "reason": "Disrespect at the door",
            "timestamp": "2026-04-19T23:10:00Z",
            "actor_username": "carlos",
        },
        {
            "guest_id": "b2c3d4e5-f6a7-8901-bcde-f12345678902",
            "displayname": "Mateo",
            "reason": None,
            "timestamp": "2026-04-05T22:40:00Z",
            "actor_username": None,
        },
    ],
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {"attended": 52, "paid_share": 0.7, "avg_peak_checkins": 12.0, "bans": 1},
}

OPERATIONS_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Door operations of finalized meetups: guest types, payments, check-ins and bans",
            "content": {
                "application/json": {
                    "examples": {
                        "operations": {
                            "summary": "A period compared with the previous period",
                            "value": OPERATIONS_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)


# -- GET /organizations/{org_id}/stats/weather --

WEATHER_STATS_EXAMPLE = {
    "event": None,
    "meetups": [
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "Alter Abril",
            "date": "2026-04-05T22:00:00Z",
            "attended": 30,
            "no_show_rate": 0.2368,
            "avg_temperature": 11.4,
            "rained": True,
            "total_precipitation_mm": 3.2,
            "has_weather": True,
        },
        {
            "meetup_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "name": "Alter Abril II",
            "date": "2026-04-19T22:00:00Z",
            "attended": 31,
            "no_show_rate": 0.1143,
            "avg_temperature": 18.0,
            "rained": False,
            "total_precipitation_mm": 0.0,
            "has_weather": True,
        },
        {
            "meetup_id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
            "name": "Alter Mayo",
            "date": "2026-05-03T22:00:00Z",
            "attended": 28,
            "no_show_rate": None,
            "avg_temperature": None,
            "rained": None,
            "total_precipitation_mm": None,
            "has_weather": False,
        },
    ],
    "rainy": {"events": 1, "avg_attended": 30.0, "no_show_rate": 0.2368},
    "dry": {"events": 1, "avg_attended": 31.0, "no_show_rate": 0.1143},
    "temperature_bands": [
        {"band": "BELOW_10", "events": 0, "avg_attended": None, "no_show_rate": None},
        {"band": "FROM_10_TO_15", "events": 1, "avg_attended": 30.0, "no_show_rate": 0.2368},
        {"band": "FROM_15_TO_20", "events": 1, "avg_attended": 31.0, "no_show_rate": 0.1143},
        {"band": "FROM_20_TO_25", "events": 0, "avg_attended": None, "no_show_rate": None},
        {"band": "FROM_25", "events": 0, "avg_attended": None, "no_show_rate": None},
    ],
    "without_weather": 1,
    "comparison_kind": None,
    "comparison": None,
}

WEATHER_STATS_EVENT_EXAMPLE = {
    "event": {
        "status": "OK",
        "weather": {
            "avg_temperature": 11.4,
            "avg_apparent_temperature": 9.8,
            "total_precipitation_mm": 3.2,
            "max_wind_kmh": 21.5,
            "rained": True,
            "fetched_at": "2026-04-06T09:00:00Z",
            "source": "open-meteo-historical-forecast",
        },
        "ready": True,
        "attended": 30,
        "no_show_rate": 0.2368,
    },
    "meetups": [],
    "rainy": None,
    "dry": None,
    "temperature_bands": [],
    "without_weather": 0,
    "comparison_kind": None,
    "comparison": None,
}

WEATHER_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Weather against the attendance of finalized meetups",
            "content": {
                "application/json": {
                    "examples": {
                        "aggregate": {
                            "summary": "A period: rainy and dry groups, temperature bands, meetups without weather",
                            "value": WEATHER_STATS_EXAMPLE,
                        },
                        "event": {
                            "summary": "One meetup (meetup_id): its weather and attendance",
                            "value": WEATHER_STATS_EVENT_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)
