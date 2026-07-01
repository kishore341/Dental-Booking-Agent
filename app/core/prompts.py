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

FULL_EXTRACTION_INSTRUCTIONS = f"""The caller is talking to a dental clinic's \
booking agent. From their MOST RECENT message only, extract any of the \
following that they clearly stated. Do not guess or infer anything not \
clearly present -- use null for anything not mentioned in this message.

- patient_name: the caller's full name, if stated
- service: map their description to exactly one of these codes: \
{SERVICE_LIST}, or null if no service is mentioned or it's ambiguous
- preferred_date: an ISO date (YYYY-MM-DD) if a specific date is mentioned. \
Resolve relative expressions like "tomorrow" or "next Monday" against the \
reference date given below. Use null if no date is mentioned.
- preferred_time: a 24-hour HH:MM time if a specific time is mentioned, or \
null if no time is mentioned.
- confirmed: true if the caller is clearly confirming a proposed booking \
(e.g. "yes", "that works", "book it"), false if clearly declining, or null \
if this message isn't a yes/no response to a confirmation.

A single message can contain several of these at once (e.g. "I need a \
cleaning tomorrow at 2pm" has both service and date/time) -- extract every \
field that's actually present, don't stop at the first one.

Respond as a single JSON object with exactly these five keys: patient_name, \
service, preferred_date, preferred_time, confirmed."""


def build_full_extraction_prompt(today_context: str) -> str:
    return "\n\n".join([SYSTEM_PREAMBLE, FULL_EXTRACTION_INSTRUCTIONS, today_context])


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


# Retry copy -- shown when extraction found nothing usable for the current
# stage. Deliberately more specific than the first-ask prompt: repeating the
# exact same line on a miss reads as "stuck" to the caller, so retries name
# the valid options explicitly.
def retry_prompt_for_stage(stage: ConversationStage, slots) -> str:
    if stage == ConversationStage.COLLECT_NAME:
        return "Sorry, I didn't catch a name there. Could you tell me your full name?"
    if stage == ConversationStage.COLLECT_SERVICE:
        readable = ", ".join(s.value.replace("_", " ") for s in ServiceType)
        return (
            f"I didn't catch a specific service there. We offer: {readable}. "
            "Which of these would you like to book?"
        )
    if stage == ConversationStage.COLLECT_DATETIME:
        return (
            "I didn't catch a specific date and time. Could you give me a day "
            "and time, like 'next Tuesday at 3 PM'?"
        )
    if stage == ConversationStage.CONFIRM:
        return "Sorry, was that a yes or a no on the booking?"
    return "Sorry, could you say that again?"
