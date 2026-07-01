"""Entity/intent extraction using LangChain with Groq as the LLM backend.

We deliberately use a *thin* LangChain usage: ChatGroq + a JSON-mode call
per turn. This is intentional -- for a linear 4-slot form-fill flow, a
heavyweight agent/tool-calling loop inside LangChain would add latency and
non-determinism without adding value. The state machine (state_machine.py)
is what actually owns control flow; LangChain here is just doing NLU.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.core.prompts import build_extraction_prompt
from app.models.enums import ConversationStage

logger = logging.getLogger(__name__)

_llm: ChatGroq | None = None


def get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        settings = get_settings()
        _llm = ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=0,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    return _llm


def extract_slots(stage: ConversationStage, user_utterance: str) -> dict:
    """Runs one Groq call to extract structured slot data for the given
    stage. Returns a dict matching the JSON shape described in prompts.py.
    Falls back to an empty dict (i.e. "extracted nothing") on any failure --
    the state machine treats that as "ask again", never as a crash."""

    extra_context = ""
    if stage == ConversationStage.COLLECT_DATETIME:
        extra_context = f"Today's date is {date.today().isoformat()}."

    system_prompt = build_extraction_prompt(stage, extra_context)

    try:
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_utterance),
            ]
        )
        content = response.content
        parsed = json.loads(content)
        logger.info("extraction stage=%s input=%r -> %r", stage, user_utterance, parsed)
        return parsed
    except Exception:  # noqa: BLE001 -- extraction must never crash the webhook
        logger.exception("extraction_failed stage=%s input=%r", stage, user_utterance)
        return {}
