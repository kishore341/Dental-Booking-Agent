"""Run this ONCE, locally, to authorize Google Calendar access with your own
Google account (no service-account key needed -- see calendar_service.py
docstring for why).

Prerequisites:
  1. In Google Cloud Console, enable the "Google Calendar API" on your project.
  2. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
     -> Application type: "Desktop app" -> download the JSON.
  3. Save that file as credentials/google-oauth-client-secret.json

Usage:
    python scripts/generate_calendar_token.py

This opens a browser window, you log into the Google account whose calendar
you want to book into, approve the "See, edit, share, and permanently
delete all the calendars..." scope, and it writes
credentials/calendar-token.json -- a refresh token that calendar_service.py
uses for every future API call. You do NOT need to run this again unless
you delete that token file or revoke access.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    settings = get_settings()
    flow = InstalledAppFlow.from_client_secrets_file(
        settings.google_calendar_client_secret_path, SCOPES
    )
    creds = flow.run_local_server(port=0)

    with open(settings.google_calendar_token_path, "w") as f:
        f.write(creds.to_json())

    print(f"\nSaved refresh token to {settings.google_calendar_token_path}")
    print("You will not need to run this script again unless you revoke access.")


if __name__ == "__main__":
    main()
