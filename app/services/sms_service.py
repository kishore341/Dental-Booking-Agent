from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from twilio.rest import Client

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def send_confirmation_sms(
    to_number: str, patient_name: str, service: str, start_dt: datetime
) -> str:
    settings = get_settings()
    body = (
        f"Hi {patient_name}, your {service.replace('_', ' ')} appointment at "
        f"{settings.clinic_name} is confirmed for {start_dt.strftime('%b %d at %I:%M %p')}. "
        "Reply CANCEL to cancel."
    )
    client = _get_client()
    message = client.messages.create(body=body, from_=settings.twilio_from_number, to=to_number)
    logger.info("sms_sent sid=%s to=%s", message.sid, to_number)
    return message.sid
