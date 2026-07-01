from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Groq / LangChain ---
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Firebase / Firestore ---
    # Path to a service-account JSON file. In production (Railway/Render)
    # set FIREBASE_CREDENTIALS_JSON to the raw JSON contents instead of a
    # path, and we write it to a temp file at startup (see utils/firebase_init.py)
    firebase_credentials_path: str = "credentials/firebase-service-account.json"
    firebase_credentials_json: str = ""
    firestore_project_id: str = ""

    # --- Google Calendar (OAuth user credentials, not a service-account key) ---
    google_calendar_client_secret_path: str = "credentials/google-oauth-client-secret.json"
    google_calendar_token_path: str = "credentials/calendar-token.json"
    google_calendar_token_json: str = ""  # raw token.json contents, for deploy envs
    google_calendar_id: str = "primary"
    clinic_timezone: str = "Asia/Kolkata"

    # --- Twilio ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # --- Admin API ---
    admin_api_key: str = "change-me"

    # --- App ---
    clinic_name: str = "SmileCare Dental"
    clinic_open_hour: int = 9
    clinic_close_hour: int = 18
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
