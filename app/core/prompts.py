"""Prompt templates used by the LangChain extraction chain.

Each stage gets a focused extraction prompt rather than one giant prompt
trying to parse everything at once -- this keeps Groq's JSON-mode output
reliable and keeps the state machine (not the LLM) in control of flow.
"""

from app.models.enums import ConversationStage, ServiceType

SERVICE_LIST = ", ".join(s.value for s in ServiceType)

SYSTEM_PREAMBLE = """You are the entity-extraction module behind a dental \
clinic's voice booking agent. You are NOT the voice the caller hears -- you \
only extract structured data from what the caller just said. Always respond \
with a single valid JSON object and nothing else. No markdown, no commentary."""

STAGE_EXTRACTION_INSTRUCTIONS: dict[ConversationStage, str] = {
    ConversationStage.COLLECT_NAME: (
        "Extract the caller's full name from their utterance. "
        'Respond as JSON: {"patient_name": "<name or null>"}'
    ),
    ConversationStage.COLLECT_SERVICE: (
        f"The caller is describing why they want an appointment. Map it to "
        f"exactly one of these service codes: {SERVICE_LIST}. "
        "If it's ambiguous or not covered, use null. "
        'Respond as JSON: {"service": "<one of the codes above or null>"}'
    ),
    ConversationStage.COLLECT_DATETIME: (
        "Extract the preferred appointment date and time from the caller's "
        "utterance. Today's reference date will be given to you -- resolve "
        "relative expressions like 'tomorrow' or 'next Monday' against it. "
        "Use ISO format. "
        'Respond as JSON: {"preferred_date": "<YYYY-MM-DD or null>", '
        '"preferred_time": "<HH:MM 24h or null>"}'
    ),
    ConversationStage.CONFIRM: (
        "Determine whether the caller is confirming or rejecting the "
        "proposed booking summary. "
        'Respond as JSON: {"confirmed": true, false, or null if unclear}'
    ),
}


def build_extraction_prompt(stage: ConversationStage, extra_context: str = "") -> str:
    instructions = STAGE_EXTRACTION_INSTRUCTIONS.get(stage, "")
    parts = [SYSTEM_PREAMBLE, instructions]
    if extra_context:
        parts.append(extra_context)
    return "\n\n".join(parts)


# Assistant-facing copy (what gets spoken back to the caller via Vapi TTS)
def prompt_for_stage(stage: ConversationStage, clinic_name: str, slots) -> str:
    if stage == ConversationStage.GREETING:
        return f"Thanks for calling {clinic_name}. Can I get your full name, please?"
    if stage == ConversationStage.COLLECT_NAME:
        return "Sorry, I didn't catch your name. Could you repeat it?"
    if stage == ConversationStage.COLLECT_SERVICE:
        return f"Thanks, {slots.patient_name}. What can we help you with today?"
    if stage == ConversationStage.COLLECT_DATETIME:
        return "Got it. What date and time works best for you?"
    if stage == ConversationStage.CONFIRM:
        return (
            f"Just to confirm: {slots.patient_name}, a {slots.service.value.replace('_', ' ')} "
            f"appointment on {slots.preferred_date} at {slots.preferred_time}. "
            "Shall I book that in?"
        )
    if stage == ConversationStage.BOOKED:
        return "You're all set! You'll get a confirmation text shortly. Anything else?"
    return "Sorry, something went wrong on our end. Let me transfer you to the front desk."
