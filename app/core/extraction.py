"""Entity/intent extraction using LangChain with Groq as the LLM backend.

We deliberately use a *thin* LangChain usage: ChatGroq + a JSON-mode call
per turn. This is intentional -- a heavyweight agent/tool-calling loop
inside LangChain would add latency and non-determinism without adding
value for what is fundamentally a 4-slot form-fill. LangChain is doing
NLU; the state machine (state_machine.py) owns control flow.

Extraction is comprehensive, not stage-scoped: every turn we ask for all
five fields (name, service, date, time, confirmation) regardless of which
ones are still missing. This is what lets a caller say "I need a root
canal tomorrow at 2pm" in one sentence and have both slots fill at once,
instead of being forced through one question per fact. The state machine
decides what to do with whatever comes back -- fields that don't apply to
the current turn are simply null and ignored.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.core.prompts import build_full_extraction_prompt

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


def extract_all_slots(user_utterance: str) -> dict:
    """Runs one Groq call that tries to pull every field it can find in
    this utterance: patient_name, service, preferred_date, preferred_time,
    confirmed. Anything not clearly stated comes back null. Falls back to
    an empty dict on any failure -- the state machine treats that as "no
    new information this turn", never as a crash."""

    today_context = f"Today's date is {date.today().isoformat()}."
    system_prompt = build_full_extraction_prompt(today_context)

    try:
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_utterance),
            ]
        )
        parsed = json.loads(response.content)
        logger.info("extraction input=%r -> %r", user_utterance, parsed)
        return parsed
    except Exception:  # noqa: BLE001 -- extraction must never crash the webhook
        logger.exception("extraction_failed input=%r", user_utterance)
        return {}
