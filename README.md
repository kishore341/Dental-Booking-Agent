# Dental Appointment Booking Agent — Backend

Backend for a voice AI dental receptionist. Sits behind a Vapi voice assistant,
owns the multi-turn booking conversation end-to-end, and books real Google
Calendar events with a Twilio SMS confirmation, logging everything to
Firestore.

## Architecture

```
Caller ↔ Vapi (STT/TTS + telephony)
              │  tool-calls webhook, once per turn
              ▼
        FastAPI backend  (this repo)
              │
   ┌──────────┼───────────────┬──────────────┐
   ▼          ▼               ▼              ▼
State FSM   LangChain+Groq  Google Calendar  Twilio
(app/core)  (extraction)     (real booking)   (SMS)
   │
   ▼
Firestore (conversation state + booking log)
```

**Key decision: Vapi is a thin voice layer, not the conversation brain.**
Vapi's assistant is configured with exactly one custom tool, `process_turn`,
which fires on every user turn and passes the raw utterance to this backend.
This backend — not Vapi's own LLM — owns the state machine
(`app/core/state_machine.py`), so "where is this caller in the flow" lives in
one place (Firestore), not split between our server and Vapi's dialog
manager. This is also what makes multi-turn state survive across separate
webhook HTTP hits: nothing is held in memory between requests, everything is
re-read from Firestore keyed by `call.id`.

**Why LangChain here is deliberately thin.** Extraction (`app/core/extraction.py`)
is one Groq call per turn with a stage-specific prompt and JSON-mode output.
A full agent/tool-loop would add latency and non-determinism for what is a
straightforward 4-slot form-fill. LangChain is doing NLU; the FSM does control
flow.

**Scaling to 1,000 clinics.** Every document (conversation, booking) already
carries `clinic_id`. The only missing piece for true multi-tenancy is a
`clinics` collection mapping Vapi `assistantId` → `clinic_id` → calendar ID /
Twilio number, looked up at the top of the webhook handler instead of the
current single hardcoded `GOOGLE_CALENDAR_ID`. State, extraction, and booking
logic don't change at all — see "What I'd do with more time" below.

## Project structure

```
app/
  main.py                  FastAPI app + router registration
  config.py                Settings (env-driven, pydantic-settings)
  api/
    webhook.py             POST /webhook/vapi — VAPI tool-calls intake
    admin.py                GET /admin/bookings, /admin/conversations/{id}
  core/
    state_machine.py       The FSM: greeting → name → service → datetime → confirm → booked
    extraction.py          LangChain + Groq entity extraction per stage
    prompts.py             Extraction prompts + assistant-facing copy
  services/
    firestore_service.py   All Firestore reads/writes
    calendar_service.py    Google Calendar freebusy check + event creation
    sms_service.py         Twilio confirmation SMS
  models/
    schemas.py             Pydantic v2 models (VAPI payloads + internal state)
    enums.py                ConversationStage, ServiceType
tests/                     pytest, all external services mocked
scripts/simulate_call.py   Fires a full 5-turn call at a running instance
```

## Setup

### 1. Groq API key
Create a key at https://console.groq.com/keys → `GROQ_API_KEY`.

### 2. Firebase / Firestore
1. https://console.firebase.google.com → create a project → enable Firestore
   (Native mode, any region).
2. Project settings → Service accounts → **Generate new private key** →
   save as `credentials/firebase-service-account.json`.
3. Set `FIRESTORE_PROJECT_ID` to the Firebase project ID.

### 3. Google Calendar

This uses **OAuth user credentials**, not a service-account key. Many GCP
projects now block service-account key creation by default (the
`iam.disableServiceAccountKeyCreation` org policy) -- this is especially
common if the project was created under a Workspace/education/company
Google account rather than a plain personal `@gmail.com` account. OAuth
sidesteps this entirely: you authorize with your own Google account once,
and there's no "share the calendar with a service account" step either.

1. https://console.cloud.google.com → new or existing project → enable
   **Google Calendar API** (APIs & Services → Library → search "Google
   Calendar API" → Enable).
2. APIs & Services → Credentials → **Create Credentials → OAuth client ID**.
   - If prompted, configure the OAuth consent screen first: User type
     "External", app name anything, your email as support/developer
     contact, and add your own Google account under "Test users" (this
     keeps the app in testing mode, which is fine -- no Google review
     needed).
   - Application type: **Desktop app** → Create → **Download JSON**.
3. Save that file as `credentials/google-oauth-client-secret.json`.
4. Run the one-time authorization script:
   ```bash
   python scripts/generate_calendar_token.py
   ```
   A browser window opens → log in with the Google account whose calendar
   you want to book into → approve access → it saves
   `credentials/calendar-token.json`. You won't need to repeat this unless
   you revoke access or delete that file.
5. Leave `GOOGLE_CALENDAR_ID=primary` if you're booking into that same
   account's main calendar, or use a specific calendar's ID from
   Settings → *Integrate calendar* if you created a separate test calendar
   under that account.

**If Firestore's service-account key creation (step 2 above) also fails
with the same `iam.disableServiceAccountKeyCreation` error:** the fastest
fix is to create the Firebase project itself under a plain personal
`@gmail.com` account rather than a Workspace/company/college account --
personal consumer accounts normally have no organization node, so this
policy isn't enforced on them at all. Recreate the Firebase project there
and generate the service-account key again.

### 4. Twilio
1. https://console.twilio.com → free trial account → note the **Account
   SID** and **Auth Token**.
2. For `TWILIO_FROM_NUMBER`, use a trial number Twilio gives you, or one of
   Twilio's [magic test numbers](https://www.twilio.com/docs/iam/test-credentials)
   during development (`+15005550006` is the standard "valid" test number).
3. On a trial account you can only SMS numbers you've verified in the
   console — verify your own phone to test real delivery.

### 5. Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the keys above
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/health` to confirm it's up.

### 6. Run the tests

```bash
pytest tests/ -v
```

All 14 tests mock Firestore/Calendar/Twilio/Groq, so they run with no
credentials at all — this is what CI would run.

### 7. Simulate a full call locally

```bash
python scripts/simulate_call.py --url http://localhost:8000
```

Walks a fake caller through all 5 turns and prints what the agent would say
at each step. Requires real credentials configured (this hits real Groq +
real Calendar + real Twilio).

## Deploying (Railway)

1. Push this repo to GitHub.
2. https://railway.app → New Project → Deploy from GitHub repo.
3. Add all variables from `.env.example` under Variables — for
   `FIREBASE_CREDENTIALS_JSON` and `GOOGLE_CALENDAR_CREDENTIALS_JSON`, paste
   the **entire contents** of each service-account JSON file as the value
   (the code detects these and writes them to a temp file at runtime, so you
   don't need to ship secret files in the repo).
4. Railway auto-detects `railway.json` / the `Procfile` and runs
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Once deployed, hit `https://<your-app>.up.railway.app/health`.

(Render/Vercel work the same way — same start command, same env vars.)

## Wiring up Vapi

On your Vapi assistant, add one custom tool and point its server URL at your
deployment:

```json
{
  "type": "function",
  "function": {
    "name": "process_turn",
    "description": "Send the caller's latest utterance to the booking backend and get back what to say next.",
    "parameters": {
      "type": "object",
      "properties": {
        "utterance": {"type": "string"},
        "caller_phone_number": {"type": "string"}
      },
      "required": ["utterance"]
    }
  },
  "server": {"url": "https://<your-deploy>/webhook/vapi"}
}
```

Give the assistant a system prompt instructing it to call `process_turn` on
every user turn and speak back exactly what the tool returns.

## Admin API

```bash
curl https://<your-deploy>/admin/bookings \
  -H "x-admin-api-key: <ADMIN_API_KEY>"

curl https://<your-deploy>/admin/conversations/<call_id> \
  -H "x-admin-api-key: <ADMIN_API_KEY>"
```

## What I'd do with more time

- **Multi-tenant clinic lookup.** Add a `clinics` collection keyed by Vapi
  `assistantId`, resolved at the top of the webhook handler, instead of one
  hardcoded calendar/Twilio number.
- **Webhook signature verification.** Vapi supports HMAC-signed webhooks;
  I'd verify `x-vapi-signature` before processing anything, especially once
  this is handling real PHI-adjacent data.
- **Idempotency.** Vapi can retry a webhook on timeout; `toolCallId` should
  be checked against a dedup table so a retried "book it" doesn't create two
  calendar events.
- **Async booking.** Calendar + SMS calls currently happen inline in the
  webhook response path. At scale I'd ack the tool call fast and do the
  actual booking as a background task, speaking a "let me confirm that"
  holding line if needed.
- **Cancellation/reschedule flow.** Out of scope for the 4-slot happy path
  but the FSM shape extends cleanly — a `MODIFY` stage branching off `BOOKED`.
