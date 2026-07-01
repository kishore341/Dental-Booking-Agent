"""Real Google Calendar API integration (no mocking).

Auth model: OAuth 2.0 *user* credentials, not a service-account key.

Why: many GCP projects (including personal ones, via Google's "Secure by
Default" org policies) now block `iam.disableServiceAccountKeyCreation` by
default, so a service-account JSON key often can't be created at all. OAuth
user credentials sidestep that entirely -- you authorize once with your own
Google account (scripts/generate_calendar_token.py), which produces a
long-lived refresh token stored in `credentials/calendar-token.json`. Every
API call after that uses the refresh token to mint short-lived access
tokens automatically, no manual re-auth needed. This also means the target
calendar doesn't need to be "shared" with anything -- if it's the same
Google account that ran the authorization script, `GOOGLE_CALENDAR_ID=primary`
just works.

`clinic_id` is accepted on every function for forward-compatibility with
multi-clinic routing (clinic_id -> calendar_id lookup) even though this
build only wires up one calendar via GOOGLE_CALENDAR_ID.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timedelta
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import pytz

from app.config import get_settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None


def _load_credentials() -> Credentials:
    settings = get_settings()

    if settings.google_calendar_token_json:
        token_data = json.loads(settings.google_calendar_token_json)
    else:
        with open(settings.google_calendar_token_path) as f:
            token_data = json.load(f)

    creds = Credentials.from_authorized_user_info(token_data, scopes=SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds


def _get_service():
    global _service
    if _service is not None:
        return _service

    creds = _load_credentials()
    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _calendar_id_for_clinic(clinic_id: str) -> str:
    # Single-clinic build: one calendar. Multi-tenant version would look
    # this up from a `clinics` Firestore collection keyed by clinic_id.
    return get_settings().google_calendar_id


def is_slot_available(
    clinic_id: str, date_str: str, time_str: str, duration_minutes: int
) -> bool:
    settings = get_settings()
    tz = pytz.timezone(settings.clinic_timezone)
    start = tz.localize(datetime.fromisoformat(f"{date_str}T{time_str}"))
    end = start + timedelta(minutes=duration_minutes)

    if start.hour < settings.clinic_open_hour or end.hour > settings.clinic_close_hour:
        return False

    service = _get_service()
    calendar_id = _calendar_id_for_clinic(clinic_id)
    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "timeZone": settings.clinic_timezone,
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy_slots = result["calendars"][calendar_id]["busy"]
    return len(busy_slots) == 0


def create_booking_event(
    clinic_id: str, summary: str, start_dt: datetime, duration_minutes: int
) -> str:
    settings = get_settings()
    tz = pytz.timezone(settings.clinic_timezone)
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    service = _get_service()
    calendar_id = _calendar_id_for_clinic(clinic_id)

    event = {
        "summary": summary,
        "description": "Booked automatically via the voice booking agent.",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": settings.clinic_timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": settings.clinic_timezone},
    }
    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    logger.info("calendar_event_created id=%s summary=%s", created.get("id"), summary)
    return created.get("id")
