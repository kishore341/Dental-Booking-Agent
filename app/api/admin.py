from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.models.schemas import BookingListResponse, ConversationHistoryResponse
from app.services.firestore_service import (
    get_conversation_state,
    list_bookings,
    list_conversations,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin_key(x_admin_api_key: str = Header(...)) -> None:
    settings = get_settings()
    if x_admin_api_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin API key")


@router.get("/bookings", response_model=BookingListResponse, dependencies=[Depends(require_admin_key)])
def get_bookings(clinic_id: Optional[str] = None, limit: int = 100):
    bookings = list_bookings(clinic_id=clinic_id, limit=limit)
    return BookingListResponse(count=len(bookings), bookings=bookings)


@router.get(
    "/conversations/{call_id}",
    response_model=ConversationHistoryResponse,
    dependencies=[Depends(require_admin_key)],
)
def get_conversation(call_id: str):
    state = get_conversation_state(call_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationHistoryResponse(
        call_id=state.call_id, stage=state.stage, slots=state.slots, history=state.history
    )


@router.get("/conversations", dependencies=[Depends(require_admin_key)])
def list_all_conversations(clinic_id: Optional[str] = None, limit: int = 100):
    states = list_conversations(clinic_id=clinic_id, limit=limit)
    return {
        "count": len(states),
        "conversations": [
            {"call_id": s.call_id, "stage": s.stage, "updated_at": s.updated_at} for s in states
        ],
    }
