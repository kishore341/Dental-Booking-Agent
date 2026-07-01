"""The booking flow state machine.

Design notes (mirrors what should go in the Loom video):

- One ConversationState document per call_id, persisted in Firestore.
  Firestore is the source of truth, not memory -- this backend is stateless
  between requests, which is what lets it scale horizontally / across
  clinics without sticky sessions.
- This is dynamic slot-filling, not a rigid linear script. Every turn runs
  one comprehensive extraction (extract_all_slots) that pulls whatever
  fields are present in that utterance -- name, service, date/time,
  confirmation -- regardless of which ones are still missing. The *next
  question asked* is derived fresh each turn from whichever slot is still
  empty (_next_missing_stage), not from a fixed step counter. A caller who
  front-loads everything ("I'm Asha, I need a cleaning tomorrow at 2pm")
  skips straight to the confirmation summary in one turn; a caller who
  only gives one fact at a time gets asked one thing at a time. Either way
  the same code path handles it.
- Multi-tenancy: `clinic_id` is threaded through every layer (state,
  calendar, SMS, bookings) even though this assignment only wires up one
  clinic. Swapping in a clinic-lookup-by-Vapi-assistantId is a small change,
  not a redesign -- see README "Scaling to 1,000 clinics".
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.core.extraction import extract_all_slots
from app.core.prompts import prompt_for_stage, retry_prompt_for_stage
from app.models.enums import ConversationStage, ServiceType
from app.models.schemas import ConversationState, ConversationTurn, SlotData
from app.services import calendar_service, sms_service
from app.services.firestore_service import (
    get_conversation_state,
    save_booking,
    save_conversation_state,
)

logger = logging.getLogger(__name__)

MAX_RETRIES_PER_STAGE = 3


def _next_missing_stage(slots: SlotData) -> ConversationStage:
    """The single source of truth for 'what do we still need to ask'.
    Recomputed fresh every turn from the slots actually filled so far --
    this is what lets the flow skip stages when a caller volunteers
    multiple facts in one message."""
    if not slots.patient_name:
        return ConversationStage.COLLECT_NAME
    if not slots.service:
        return ConversationStage.COLLECT_SERVICE
    if not slots.preferred_date or not slots.preferred_time:
        return ConversationStage.COLLECT_DATETIME
    if not slots.confirmed:
        return ConversationStage.CONFIRM
    return ConversationStage.BOOKED


def _merge_basic_slots(slots: SlotData, extracted: dict) -> bool:
    """Merges name/service from this turn's extraction into the slots.
    Date/time are handled separately by the caller since they need an
    availability check before being accepted. Returns True if anything
    was actually filled."""
    changed = False
    name = extracted.get("patient_name")
    if name:
        slots.patient_name = str(name).strip()
        changed = True
    service_raw = extracted.get("service")
    if service_raw and service_raw in ServiceType._value2member_map_:
        slots.service = ServiceType(service_raw)
        changed = True
    return changed


def handle_turn(call_id: str, user_utterance: str, clinic_id: str = "default") -> str:
    """Main entry point called once per webhook `tool-calls` hit.
    Returns the text the assistant should speak next."""

    state = get_conversation_state(call_id)
    if state is None:
        state = ConversationState(call_id=call_id, clinic_id=clinic_id)

    is_first_turn = state.stage == ConversationStage.GREETING
    state.history.append(ConversationTurn(role="user", text=user_utterance, stage=state.stage))

    if state.stage in (ConversationStage.BOOKED, ConversationStage.FAILED):
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    extracted = extract_all_slots(user_utterance)
    changed = _merge_basic_slots(state.slots, extracted)

    pref_date = extracted.get("preferred_date")
    pref_time = extracted.get("preferred_time")
    if pref_date and pref_time:
        duration = _service_duration(state.slots.service)
        available = calendar_service.is_slot_available(
            clinic_id=state.clinic_id,
            date_str=pref_date,
            time_str=pref_time,
            duration_minutes=duration,
        )
        if not available:
            reply = (
                f"That slot on {pref_date} at {pref_time} isn't available. "
                "Could you try another time?"
            )
            return _finish_turn(state, reply)
        state.slots.preferred_date = pref_date
        state.slots.preferred_time = pref_time
        changed = True

    if is_first_turn:
        # Move off the placeholder GREETING stage so the comparisons below
        # behave consistently, whether or not this first message already
        # contained useful info.
        state.stage = ConversationStage.COLLECT_NAME

    target_stage = _next_missing_stage(state.slots)

    if target_stage == ConversationStage.CONFIRM:
        if state.stage == ConversationStage.CONFIRM:
            # Summary was already read out on a previous turn -- this turn
            # should be a yes/no answer to it.
            confirmed = extracted.get("confirmed")
            if confirmed is True:
                reply = _finalize_booking(state)
                return _finish_turn(state, reply)
            if confirmed is False:
                state.slots.preferred_date = None
                state.slots.preferred_time = None
                state.stage = ConversationStage.COLLECT_DATETIME
                state.retry_count = 0
                reply = "No problem, what date and time would work better?"
                return _finish_turn(state, reply)
            return _retry_or_bail(state, user_utterance)

        # First time all slots are complete -- read back the summary and
        # wait for an explicit yes, even if this same message already
        # contained something that looked like a confirmation. Never book
        # without the caller hearing the summary first.
        state.stage = ConversationStage.CONFIRM
        state.retry_count = 0
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if target_stage != state.stage or changed:
        state.stage = target_stage
        state.retry_count = 0
        reply = prompt_for_stage(state.stage, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    if is_first_turn:
        # Nothing usable in the caller's opening line -- this is the actual
        # first ask, not a failed-extraction retry, so use the welcome copy.
        reply = prompt_for_stage(ConversationStage.GREETING, _clinic_name(), state.slots)
        return _finish_turn(state, reply)

    return _retry_or_bail(state, user_utterance)


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
    reply = retry_prompt_for_stage(state.stage, state.slots)
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
