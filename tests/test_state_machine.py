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


def test_greeting_advances_to_collect_name(in_memory_store):
    from app.core.state_machine import handle_turn

    reply = handle_turn("call-1", "hello")
    state = in_memory_store["call-1"]
    assert state.stage == ConversationStage.COLLECT_NAME
    assert "name" in reply.lower()


def test_full_happy_path(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_slots") as mock_extract, patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=True
    ), patch(
        "app.core.state_machine.calendar_service.create_booking_event", return_value="evt_123"
    ), patch(
        "app.core.state_machine.save_booking"
    ) as mock_save_booking, patch(
        "app.core.state_machine.sms_service.send_confirmation_sms", return_value="SM123"
    ):
        mock_save_booking.return_value.model_dump.return_value = {}

        sm.handle_turn("call-2", "hi")  # greeting -> collect_name

        mock_extract.return_value = {"patient_name": "Asha Rao"}
        sm.handle_turn("call-2", "My name is Asha Rao")
        assert in_memory_store["call-2"].stage == ConversationStage.COLLECT_SERVICE

        mock_extract.return_value = {"service": "cleaning"}
        sm.handle_turn("call-2", "I need a cleaning")
        assert in_memory_store["call-2"].slots.service == ServiceType.CLEANING
        assert in_memory_store["call-2"].stage == ConversationStage.COLLECT_DATETIME

        mock_extract.return_value = {"preferred_date": "2026-07-10", "preferred_time": "10:00"}
        sm.handle_turn("call-2", "Next Friday at 10am")
        assert in_memory_store["call-2"].stage == ConversationStage.CONFIRM

        mock_extract.return_value = {"confirmed": True}
        reply = sm.handle_turn("call-2", "Yes that works")
        assert in_memory_store["call-2"].stage == ConversationStage.BOOKED
        assert "all set" in reply.lower()


def test_unavailable_slot_does_not_advance_stage(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_slots") as mock_extract, patch(
        "app.core.state_machine.calendar_service.is_slot_available", return_value=False
    ):
        sm.handle_turn("call-3", "hi")
        mock_extract.return_value = {"patient_name": "Ravi"}
        sm.handle_turn("call-3", "Ravi")
        mock_extract.return_value = {"service": "checkup"}
        sm.handle_turn("call-3", "checkup please")

        mock_extract.return_value = {"preferred_date": "2026-07-10", "preferred_time": "10:00"}
        reply = sm.handle_turn("call-3", "Friday 10am")

        assert in_memory_store["call-3"].stage == ConversationStage.COLLECT_DATETIME
        assert "isn't available" in reply


def test_max_retries_moves_to_failed(in_memory_store):
    from app.core import state_machine as sm

    with patch("app.core.state_machine.extract_slots", return_value={}):
        sm.handle_turn("call-4", "hi")  # -> collect_name
        for _ in range(sm.MAX_RETRIES_PER_STAGE):
            sm.handle_turn("call-4", "mumble mumble")
        assert in_memory_store["call-4"].stage == ConversationStage.FAILED
