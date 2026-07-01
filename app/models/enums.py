from enum import Enum


class ConversationStage(str, Enum):
    """Ordered stages of the booking flow. Order matters — the state
    machine advances linearly through these unless a correction is detected."""

    GREETING = "greeting"
    COLLECT_NAME = "collect_name"
    COLLECT_SERVICE = "collect_service"
    COLLECT_DATETIME = "collect_datetime"
    CONFIRM = "confirm"
    BOOKED = "booked"
    FAILED = "failed"


class ServiceType(str, Enum):
    CLEANING = "cleaning"
    CHECKUP = "checkup"
    FILLING = "filling"
    ROOT_CANAL = "root_canal"
    EXTRACTION = "extraction"
    WHITENING = "whitening"
    ORTHODONTIC_CONSULT = "orthodontic_consult"
    EMERGENCY = "emergency"


SERVICE_DURATIONS_MINUTES = {
    ServiceType.CLEANING: 30,
    ServiceType.CHECKUP: 20,
    ServiceType.FILLING: 45,
    ServiceType.ROOT_CANAL: 90,
    ServiceType.EXTRACTION: 45,
    ServiceType.WHITENING: 60,
    ServiceType.ORTHODONTIC_CONSULT: 30,
    ServiceType.EMERGENCY: 30,
}
