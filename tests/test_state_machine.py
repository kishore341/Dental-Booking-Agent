from unittest.mock import patch

import pytest

from app.models.enums import ConversationStage, ServiceType
from app.models.schemas import ConversationState


@pytest.fixture
def in_memory_store():
    """Replaces Firestore reads/writes with an in-memory dict for these tests,
    so state_machine logic is tested without needing real Firebase creds."""
    store: dict[str, ConversationState] = {}

    def _get(call_id):
        return store.get(call_id)

    def _save(state):
        store[state.call_id] = state

    with patch("app.core.state_machine.get_conversation_state", side_effect=_get), patch(
        "app.core.state_machine.save_conversation_state", side_effect=_save
    ):
        yield store


def _no_slots():
    return {
        "patient_name": None,
        "service": None,
        "preferred_date": None,
        "preferred_time": None,
        "confirmed": None,
    }


def test_greeting_with_no_info_gives_welcome_ask_not_retry_text(in_memory_store):
    """First message contains nothing extractable -- must show the proper
    welcome/ask, not the 'sorry I didn't catch that' retry line."""
    from app.core.state_machine import handle_turn

    with patch("app.core.state_machine.extract_all_slots", return_value=_no_slots()):
        reply = handle_turn("call-1", "hello")

    state = in_memory_store["call-1"]
    assert state.stage == ConversationStage.COLLECT_NAME
    assert reply == "Thanks for calling SmileCare Dental. Can I get your full name, please?"
    assert "didn't catch" not in reply.lower()


def test_first_message_with_name_skips_straight_to_service_question(in_memory_store):
    """Flexible slot-filling: if the caller volunteers their name on the
    very first turn, don't ask for it again -- jump straight to the next
    missing slot."""
    from app.core.state_machine import handle_turn

    extracted = _no_slots() | {"patient_name": "Shanmukh"}
    with patch("app.core.state_machine.extract_all_slots", return_value=extracted):
        reply = handle_turn("call-2", "hai my name is shanmukh")

    state = in_memory_store["call-2"]
    assert state.slots.patient_name == "Shanmukh"
    assert state.stage == ConversationStage.COLLECT_SERVICE
    assert "Shanmukh" in reply
    assert "what can we help" in reply.lower()


def test_single_utterance_fills_service_and_datetime_together(in_memory_store):
    """The core fix: 'I need a root canal tomorrow at 2pm' should fill BOTH
    service and date/time from one message, jumping straight to CONFIRM,
    instead of asking for date/time again after already being given it."""
    from app.core.state_machine import handle_turn

    with patch("app.core.state_machine.extract_all_slots", return_value=_no_slots() | {"patient_name": "Ravi"}):
        handle_turn("call-3", "hi, I'm Ravi")

    combined = _no_slots() | {
        "service": "root_canal",
        "preferred_date": "2026-07-03",
        "preferred_time": "14:00",
    }
    with patch("app.core.state_machine.extract_all_slots", return_value=combined), patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=True
    ):
        reply = handle_turn("call-3", "I need a root canal tomorrow at 2pm")

    state = in_memory_store["call-3"]
    assert state.slots.service == ServiceType.ROOT_CANAL
    assert state.slots.preferred_date == "2026-07-03"
    assert state.slots.preferred_time == "14:00"
    assert state.stage == ConversationStage.CONFIRM
    assert "Shall I book that in" in reply


def test_full_happy_path_one_fact_at_a_time(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_all_slots") as mock_extract, patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=True
    ), patch(
        "app.core.state_machine.calendar_service.create_booking_event", return_value="evt_123"
    ), patch(
        "app.core.state_machine.save_booking"
    ) as mock_save_booking, patch(
        "app.core.state_machine.sms_service.send_confirmation_sms", return_value="SM123"
    ):
        mock_save_booking.return_value.model_dump.return_value = {}

        mock_extract.return_value = _no_slots()
        sm.handle_turn("call-4", "hi")

        mock_extract.return_value = _no_slots() | {"patient_name": "Asha Rao"}
        sm.handle_turn("call-4", "My name is Asha Rao")
        assert in_memory_store["call-4"].stage == ConversationStage.COLLECT_SERVICE

        mock_extract.return_value = _no_slots() | {"service": "cleaning"}
        sm.handle_turn("call-4", "I need a cleaning")
        assert in_memory_store["call-4"].slots.service == ServiceType.CLEANING
        assert in_memory_store["call-4"].stage == ConversationStage.COLLECT_DATETIME

        mock_extract.return_value = _no_slots() | {"preferred_date": "2026-07-10", "preferred_time": "10:00"}
        sm.handle_turn("call-4", "Next Friday at 10am")
        assert in_memory_store["call-4"].stage == ConversationStage.CONFIRM

        mock_extract.return_value = _no_slots() | {"confirmed": True}
        reply = sm.handle_turn("call-4", "Yes that works")
        assert in_memory_store["call-4"].stage == ConversationStage.BOOKED
        assert "all set" in reply.lower()


def test_unavailable_slot_does_not_advance_stage(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_all_slots") as mock_extract, patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=False
    ):
        mock_extract.return_value = _no_slots()
        sm.handle_turn("call-5", "hi")
        mock_extract.return_value = _no_slots() | {"patient_name": "Ravi"}
        sm.handle_turn("call-5", "Ravi")
        mock_extract.return_value = _no_slots() | {"service": "checkup"}
        sm.handle_turn("call-5", "checkup please")

        mock_extract.return_value = _no_slots() | {"preferred_date": "2026-07-10", "preferred_time": "10:00"}
        reply = sm.handle_turn("call-5", "Friday 10am")

        assert in_memory_store["call-5"].stage == ConversationStage.COLLECT_DATETIME
        assert "isn't available" in reply


def test_declining_confirmation_resets_datetime_and_asks_again(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_all_slots") as mock_extract, patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=True
    ):
        mock_extract.return_value = _no_slots() | {"patient_name": "Meera"}
        sm.handle_turn("call-6", "Meera")
        mock_extract.return_value = _no_slots() | {"service": "whitening"}
        sm.handle_turn("call-6", "whitening")
        mock_extract.return_value = _no_slots() | {"preferred_date": "2026-07-10", "preferred_time": "10:00"}
        sm.handle_turn("call-6", "Friday 10am")
        assert in_memory_store["call-6"].stage == ConversationStage.CONFIRM

        mock_extract.return_value = _no_slots() | {"confirmed": False}
        reply = sm.handle_turn("call-6", "no, actually not that time")

        state = in_memory_store["call-6"]
        assert state.stage == ConversationStage.COLLECT_DATETIME
        assert state.slots.preferred_date is None
        assert "better" in reply.lower()


def test_max_retries_moves_to_failed(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_all_slots", return_value=_no_slots()):
        sm.handle_turn("call-7", "hi")  # -> welcome ask, not a retry
        for _ in range(sm.MAX_RETRIES_PER_STAGE):
            sm.handle_turn("call-7", "mumble mumble")
        assert in_memory_store["call-7"].stage == ConversationStage.FAILED
