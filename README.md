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
1. https://console.firebase.google.com → create a project.
2. **Firestore Database** (left sidebar) → click **Create database**
   — this step is easy to miss: creating the Firebase project does *not*
   automatically provision a Firestore database. Choose **Start in test
   mode** and pick any location (e.g. `asia-south1` for India). Wait ~30–60
   seconds after creating it before your app can connect.
3. Project settings (gear icon) → Service accounts tab → **Generate new
   private key** → save the downloaded file as
   `credentials/firebase-service-account.json`.
4. Set `FIRESTORE_PROJECT_ID` to the Firebase project ID (visible at the
   top of the console, e.g. `dental-booking-agent-bed4e`).

**If key generation fails with `iam.disableServiceAccountKeyCreation`:**
see the note at the end of the Google Calendar section below — the fix is
the same for both services.

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
   SID** and **Auth Token** from the dashboard.
2. `TWILIO_FROM_NUMBER` = the trial phone number Twilio assigns you
   (visible on the dashboard, e.g. `+1XXXXXXXXXX`). This is the
   **sender** — don't confuse it with your own mobile number.
3. **Trial accounts can only SMS numbers you've explicitly verified.**
   Go to https://console.twilio.com/us1/develop/phone-numbers/manage/verified
   → **Add a new number** → enter your real mobile number in E.164 format
   (e.g. `+91XXXXXXXXXX`) → Twilio sends an OTP → enter it to confirm.
4. Whichever number you use as `caller_phone_number` in a webhook payload
   (or in `scripts/simulate_call.py`) **must be this verified number** —
   Twilio's published "magic test numbers" like `+15005550006` only work
   with Twilio's separate fake test credentials, not your real trial
   Account SID, and will fail with error 21608 ("unverified number") if
   you try to use them with real credentials.

### 5. Admin API key
This one isn't issued by any service — it's a password you invent yourself
to protect the `/admin/*` endpoints. Generate one:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
Put the result in `.env` as `ADMIN_API_KEY`, and send it as the
`x-admin-api-key` header on every admin request.

### 6. Local run

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in the keys above -- never put real keys in .env.example itself
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/health` to confirm it's up.

### 7. Run the tests

```bash
pytest tests/ -v
```

All 14 tests mock Firestore/Calendar/Twilio/Groq, so they run with no
credentials at all — this is what CI would run.

### 8. Simulate a full call locally

In a second terminal (same venv activated):
```bash
python scripts/simulate_call.py --url http://localhost:8000
```

Walks a fake caller through 5 turns and prints what the agent says at each
step. This hits real Groq, real Calendar, and real Twilio, so it needs step
1–5 fully done first. If you re-run it, bump the date/time in
`scripts/simulate_call.py` each time — the previous run's slot will already
show as booked on your calendar.

## Troubleshooting

**`Cloud Firestore API has not been used in project ... or it is disabled`**
→ You created the Firebase project but never clicked "Create database"
inside Firestore Database. See Firebase setup step 2 above.

**`iam.disableServiceAccountKeyCreation` when generating a service-account
key** → Your Google account is attached to an organization (common with
Workspace/company/college emails) that blocks service-account key creation
by default. For Calendar, this repo already avoids the problem entirely via
OAuth user credentials (see step 3). For Firestore, if you hit this too,
the fastest fix is creating the Firebase project under a plain personal
`@gmail.com` account, which normally has no organization policy attached.

**`groq.GroqError: The api_key client option must be set`** → `GROQ_API_KEY`
isn't reaching the app. Check `.env` has the real key with no quotes, run
`python -c "from app.config import get_settings; print(repr(get_settings().groq_api_key))"`
to confirm what's actually being loaded, and remember `uvicorn --reload`
does **not** re-read `.env` on file save — you have to fully stop (Ctrl+C)
and restart the server after changing `.env`.

**`ModuleNotFoundError: No module named 'app'` when running a script in
`scripts/`** → Run it as a module from the project root instead:
`python -m scripts.generate_calendar_token`, or set
`PYTHONPATH` to the project root first.

**Twilio error 21608, "number is unverified"** → See Twilio setup step 3.
Trial accounts require every recipient number to be explicitly verified.

**`500 Internal Server Error` hitting `/webhook/vapi` from the Swagger UI
(`/docs`)** → The endpoint expects a specific VAPI-shaped JSON body; an
empty "Try it out" request body will fail. Paste a full example payload
(see "Wiring up Vapi" below for the tool schema, or copy a payload shape
from `scripts/simulate_call.py`'s `build_payload` function).

**GitHub blocks your push with "Push cannot contain secrets"** → A real API
key ended up committed into `.env.example` at some point (it should only
ever contain placeholders). Rotate the exposed key immediately (treat
anything that touched a commit as burned), fix `.env.example` back to
placeholders, and if the leak is buried in old commits rather than just the
latest one, the cleanest fix is often a fresh orphan branch:
```bash
git checkout --orphan clean-main
git add -A
git commit -m "Dental appointment booking agent backend"
git branch -D main
git branch -m main
git push -u origin main --force
```
Only safe to force-push like this if the remote never successfully received
the bad commits in the first place (check: did any earlier push actually
succeed, or were they all rejected?).

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
