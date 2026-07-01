import json
from unittest.mock import MagicMock, patch

from app.core.extraction import extract_slots
from app.models.enums import ConversationStage


def test_extract_slots_parses_valid_json():
    fake_response = MagicMock()
    fake_response.content = json.dumps({"patient_name": "Kishore Kumar"})

    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = fake_response
        result = extract_slots(ConversationStage.COLLECT_NAME, "This is Kishore Kumar")

    assert result == {"patient_name": "Kishore Kumar"}


def test_extract_slots_returns_empty_dict_on_malformed_json():
    fake_response = MagicMock()
    fake_response.content = "not valid json {{"

    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = fake_response
        result = extract_slots(ConversationStage.COLLECT_NAME, "garbled input")

    assert result == {}


def test_extract_slots_never_raises_on_llm_exception():
    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("Groq API down")
        result = extract_slots(ConversationStage.COLLECT_SERVICE, "a cleaning please")

    assert result == {}
