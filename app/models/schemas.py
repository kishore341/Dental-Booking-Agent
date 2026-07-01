from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConversationStage, ServiceType


# ---------------------------------------------------------------------------
# VAPI webhook payload models
# Shape confirmed against docs.vapi.ai/api-reference/webhooks/server-message
# and docs.vapi.ai/tools/custom-tools. We only need to model what our
# server actually branches on -- full fidelity to every VAPI field isn't
# necessary, but the tool-calls shape below is byte-for-byte what Vapi sends.
# ---------------------------------------------------------------------------


class VapiCall(BaseModel):
    id: str
    orgId: Optional[str] = None
    assistantId: Optional[str] = None
    customer: Optional[dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class VapiFunctionCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class VapiToolCall(BaseModel):
    id: str
    type: str = "function"
    function: VapiFunctionCall


class VapiMessage(BaseModel):
    """The inner `message` object. `type` is the discriminator VAPI uses:
    tool-calls | transcript | status-update | end-of-call-report | ..."""

    type: str
    call: Optional[VapiCall] = None
    toolCalls: Optional[list[VapiToolCall]] = None
    transcript: Optional[str] = None
    role: Optional[str] = None
    artifact: Optional[dict[str, Any]] = None
    endedReason: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VapiWebhookPayload(BaseModel):
    message: VapiMessage


class VapiToolResult(BaseModel):
    toolCallId: str
    result: str


class VapiToolResultsResponse(BaseModel):
    results: list[VapiToolResult]


# ---------------------------------------------------------------------------
# Internal conversation state (persisted in Firestore, one doc per call_id)
# ---------------------------------------------------------------------------


class SlotData(BaseModel):
    patient_name: Optional[str] = None
    service: Optional[ServiceType] = None
    preferred_date: Optional[str] = None   # ISO date, e.g. 2026-07-04
    preferred_time: Optional[str] = None   # HH:MM 24h
    phone_number: Optional[str] = None
    confirmed: bool = False


class ConversationTurn(BaseModel):
    role: str            # "user" | "assistant" | "system"
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    stage: Optional[ConversationStage] = None


class ConversationState(BaseModel):
    call_id: str
    clinic_id: str = "default"
    stage: ConversationStage = ConversationStage.GREETING
    slots: SlotData = Field(default_factory=SlotData)
    history: list[ConversationTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    retry_count: int = 0


class BookingRecord(BaseModel):
    booking_id: str
    call_id: str
    clinic_id: str = "default"
    patient_name: str
    service: ServiceType
    start_time: datetime
    end_time: datetime
    phone_number: Optional[str] = None
    calendar_event_id: Optional[str] = None
    sms_sid: Optional[str] = None
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Admin API response models
# ---------------------------------------------------------------------------


class BookingListResponse(BaseModel):
    count: int
    bookings: list[BookingRecord]


class ConversationHistoryResponse(BaseModel):
    call_id: str
    stage: ConversationStage
    slots: SlotData
    history: list[ConversationTurn]
