"""All Firestore reads/writes go through this module -- nothing else in the
codebase should import firebase_admin directly. Two collections:

  conversations/{call_id}   -> ConversationState (full history + slots)
  bookings/{booking_id}     -> BookingRecord

Keeping collections flat and keyed by call_id / booking_id (rather than
nesting under a clinic doc) keeps multi-tenant queries simple: every
document carries `clinic_id`, so filtering per-clinic is one `.where()`
away without needing sub-collection gymnastics.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.models.enums import ServiceType
from app.models.schemas import BookingRecord, ConversationState
from app.utils.firebase_init import get_firestore_client

logger = logging.getLogger(__name__)

CONVERSATIONS_COLLECTION = "conversations"
BOOKINGS_COLLECTION = "bookings"


def get_conversation_state(call_id: str) -> Optional[ConversationState]:
    db = get_firestore_client()
    doc = db.collection(CONVERSATIONS_COLLECTION).document(call_id).get()
    if not doc.exists:
        return None
    return ConversationState.model_validate(doc.to_dict())


def save_conversation_state(state: ConversationState) -> None:
    db = get_firestore_client()
    data = state.model_dump(mode="json")
    db.collection(CONVERSATIONS_COLLECTION).document(state.call_id).set(data)


def list_conversations(clinic_id: Optional[str] = None, limit: int = 100) -> list[ConversationState]:
    db = get_firestore_client()
    query = db.collection(CONVERSATIONS_COLLECTION)
    if clinic_id:
        query = query.where("clinic_id", "==", clinic_id)
    query = query.limit(limit)
    return [ConversationState.model_validate(d.to_dict()) for d in query.stream()]


def save_booking(
    booking_id: str,
    call_id: str,
    clinic_id: str,
    patient_name: str,
    service: ServiceType,
    start_time: datetime,
    end_time: datetime,
    phone_number: Optional[str] = None,
    calendar_event_id: Optional[str] = None,
    sms_sid: Optional[str] = None,
    status: str = "confirmed",
    **_ignore,
) -> BookingRecord:
    booking = BookingRecord(
        booking_id=booking_id,
        call_id=call_id,
        clinic_id=clinic_id,
        patient_name=patient_name,
        service=service,
        start_time=start_time,
        end_time=end_time,
        phone_number=phone_number,
        calendar_event_id=calendar_event_id,
        sms_sid=sms_sid,
        status=status,
    )
    db = get_firestore_client()
    db.collection(BOOKINGS_COLLECTION).document(booking_id).set(booking.model_dump(mode="json"))
    return booking


def list_bookings(clinic_id: Optional[str] = None, limit: int = 100) -> list[BookingRecord]:
    db = get_firestore_client()
    query = db.collection(BOOKINGS_COLLECTION)
    if clinic_id:
        query = query.where("clinic_id", "==", clinic_id)
    query = query.limit(limit)
    return [BookingRecord.model_validate(d.to_dict()) for d in query.stream()]
