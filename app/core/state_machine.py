"""The booking flow state machine.

Design notes (mirrors what should go in the Loom video):

- One ConversationState document per call_id, persisted in Firestore.
  Firestore is the source of truth, not memory -- this backend is stateless
  between requests, which is what lets it scale horizontally / across
  clinics without sticky sessions.
- The state machine is a strict linear flow with one exception: at every
  stage we re-run extraction against the *current* utterance, so if a
  caller corrects themselves ("actually make that a filling") the slot
  gets overwritten rather than the flow breaking.
- Multi-tenancy: `clinic_id` is threaded through every layer (state,
  calendar, SMS, bookings) even though this assignment only wires up one
  clinic. Swapping in a clinic-lookup-by-Vapi-assistantId is a small change,
  not a redesign -- see README "Scaling to 1,000 clinics".
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.core.extraction import extract_slots
from app.core.prompts import prompt_for_stage
from app.models.enums import ConversationStage, ServiceType
from app.models.schemas import ConversationState, ConversationTurn
from app.services import calendar_service, sms_service
from app.services.firestore_service import (
    get_conversation_state,
    save_booking,
    save_conversation_state,
)

logger = logging.getLogger(__name__)

MAX_RETRIES_PER_STAGE = 3

STAGE_ORDER = [
    ConversationStage.GREETING,
    ConversationStage.COLLECT_NAME,
    ConversationStage.COLLECT_SERVICE,
    ConversationStage.COLLECT_DATETIME,
    ConversationStage.CONFIRM,
    ConversationStage.BOOKED,
]


def _next_stage(current: ConversationStage) -> ConversationStage:
    idx = STAGE_ORDER.index(current)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return ConversationStage.BOOKED


def handle_turn(call_id: str, user_utterance: str, clinic_id: str = "default") -> str:
    """Main entry point called once per webhook `tool-calls` hit.
    Returns the text the assistant should speak next."""

    state = get_conversation_state(call_id)
    if state is None:
        state = ConversationState(call_id=call_id, clinic_id=clinic_id)

    state.history.append(ConversationTurn(role="user", text=user_utterance, stage=state.stage))

    # GREETING has nothing to extract -- it just kicks the flow off.
    if state.stage == ConversationStage.GREETING:
        state.stage = ConversationStage.COLLECT_NAME
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if state.stage == ConversationStage.COLLECT_NAME:
        extracted = extract_slots(state.stage, user_utterance)
        name = extracted.get("patient_name")
        if not name:
            return _retry_or_bail(state, user_utterance)
        state.slots.patient_name = name.strip()
        state.stage = _next_stage(state.stage)
        state.retry_count = 0
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if state.stage == ConversationStage.COLLECT_SERVICE:
        extracted = extract_slots(state.stage, user_utterance)
        service_raw = extracted.get("service")
        if not service_raw or service_raw not in ServiceType._value2member_map_:
            return _retry_or_bail(state, user_utterance)
        state.slots.service = ServiceType(service_raw)
        state.stage = _next_stage(state.stage)
        state.retry_count = 0
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if state.stage == ConversationStage.COLLECT_DATETIME:
        extracted = extract_slots(state.stage, user_utterance)
        pref_date = extracted.get("preferred_date")
        pref_time = extracted.get("preferred_time")
        if not pref_date or not pref_time:
            return _retry_or_bail(state, user_utterance)

        available = calendar_service.is_slot_available(
            clinic_id=state.clinic_id,
            date_str=pref_date,
            time_str=pref_time,
            duration_minutes=_service_duration(state.slots.service),
        )
        if not available:
            reply = (
                f"That slot on {pref_date} at {pref_time} isn't available. "
                "Could you try another time?"
            )
            return _finish_turn(state, reply)

        state.slots.preferred_date = pref_date
        state.slots.preferred_time = pref_time
        state.stage = _next_stage(state.stage)
        state.retry_count = 0
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if state.stage == ConversationStage.CONFIRM:
        extracted = extract_slots(state.stage, user_utterance)
        confirmed = extracted.get("confirmed")
        if confirmed is None:
            return _retry_or_bail(state, user_utterance)
        if confirmed is False:
            state.stage = ConversationStage.COLLECT_DATETIME
            reply = "No problem, what date and time would work better?"
            return _finish_turn(state, reply)

        # confirmed == True -> actually book it
        reply = _finalize_booking(state)
        return _finish_turn(state, reply)

    if state.stage in (ConversationStage.BOOKED, ConversationStage.FAILED):
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    return _finish_turn(state, "Sorry, could you say that again?")


def _finalize_booking(state: ConversationState) -> str:
    try:
        start_dt = datetime.fromisoformat(f"{state.slots.preferred_date}T{state.slots.preferred_time}")
        duration = _service_duration(state.slots.service)
        event_id = calendar_service.create_booking_event(
            clinic_id=state.clinic_id,
            summary=f"{state.slots.service.value.title()} - {state.slots.patient_name}",
            start_dt=start_dt,
            duration_minutes=duration,
        )

        booking_id = str(uuid.uuid4())
        from datetime import timedelta

        booking = save_booking(
            booking_id=booking_id,
            call_id=state.call_id,
            clinic_id=state.clinic_id,
            patient_name=state.slots.patient_name,
            service=state.slots.service,
            start_time=start_dt,
            end_time=start_dt + timedelta(minutes=duration),
            phone_number=state.slots.phone_number,
            calendar_event_id=event_id,
        )

        sms_sid = None
        if state.slots.phone_number:
            sms_sid = sms_service.send_confirmation_sms(
                to_number=state.slots.phone_number,
                patient_name=state.slots.patient_name,
                service=state.slots.service.value,
                start_dt=start_dt,
            )
            booking.sms_sid = sms_sid
            save_booking(**booking.model_dump(exclude={"created_at"}))

        state.slots.confirmed = True
        state.stage = ConversationStage.BOOKED
        state.retry_count = 0
        return prompt_for_stage(state.stage, _clinic_name(), state.slots)

    except Exception:  # noqa: BLE001
        logger.exception("booking_failed call_id=%s", state.call_id)
        state.stage = ConversationStage.FAILED
        return (
            "I'm having trouble finalizing that booking. Let me transfer you "
            "to our front desk so they can get you sorted."
        )


def _retry_or_bail(state: ConversationState, user_utterance: str) -> str:
    state.retry_count += 1
    if state.retry_count >= MAX_RETRIES_PER_STAGE:
        state.stage = ConversationStage.FAILED
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)
    reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
    return _finish_turn(state, reply)


def _finish_turn(state: ConversationState, reply: str) -> str:
    state.history.append(ConversationTurn(role="assistant", text=reply, stage=state.stage))
    state.updated_at = datetime.utcnow()
    save_conversation_state(state)
    return reply


def _service_duration(service: ServiceType | None) -> int:
    from app.models.enums import SERVICE_DURATIONS_MINUTES

    if service is None:
        return 30
    return SERVICE_DURATIONS_MINUTES.get(service, 30)


def _clinic_name() -> str:
    from app.config import get_settings

    return get_settings().clinic_name
