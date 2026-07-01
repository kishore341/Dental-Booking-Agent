import json
from unittest.mock import MagicMock, patch

from app.core.extraction import extract_all_slots


def test_extract_all_slots_parses_valid_json():
    fake_response = MagicMock()
    fake_response.content = json.dumps(
        {
            "patient_name": "Kishore Kumar",
            "service": None,
            "preferred_date": None,
            "preferred_time": None,
            "confirmed": None,
        }
    )

    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = fake_response
        result = extract_all_slots("This is Kishore Kumar")

    assert result["patient_name"] == "Kishore Kumar"


def test_extract_all_slots_can_return_multiple_fields_at_once():
    """The whole point of the comprehensive extractor: one utterance can
    fill several slots simultaneously instead of one question per fact."""
    fake_response = MagicMock()
    fake_response.content = json.dumps(
        {
            "patient_name": None,
            "service": "root_canal",
            "preferred_date": "2026-07-03",
            "preferred_time": "14:00",
            "confirmed": None,
        }
    )

    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = fake_response
        result = extract_all_slots("I need a root canal tomorrow at 2pm")

    assert result["service"] == "root_canal"
    assert result["preferred_date"] == "2026-07-03"
    assert result["preferred_time"] == "14:00"


def test_extract_all_slots_returns_empty_dict_on_malformed_json():
    fake_response = MagicMock()
    fake_response.content = "not valid json {{"

    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.return_value = fake_response
        result = extract_all_slots("garbled input")

    assert result == {}


def test_extract_all_slots_never_raises_on_llm_exception():
    with patch("app.core.extraction.get_llm") as mock_get_llm:
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("Groq API down")
        result = extract_all_slots("a cleaning please")

    assert result == {}
