import logging

from fastapi import FastAPI

from app.api import admin, webhook
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = get_settings()

app = FastAPI(
    title="Dental Appointment Booking Agent",
    description="Backend for a voice AI dental receptionist. Handles VAPI "
    "webhook intake, multi-turn conversation state, Google Calendar "
    "booking, Twilio SMS confirmation, and Firestore logging.",
    version="1.0.0",
)

app.include_router(webhook.router)
app.include_router(admin.router)


@app.get("/")
def root():
    return {
        "service": "dental-booking-agent",
        "status": "ok",
        "clinic": settings.clinic_name,
        "environment": settings.environment,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
