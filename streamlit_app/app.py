"""Streamlit dashboard for the Dental Appointment Booking Agent backend.

This is a pure frontend -- it talks to the FastAPI backend over HTTP using
the same /admin/* and /webhook/vapi endpoints any other client would use.
No direct database or business logic lives here; that separation matters
for the assignment's architecture story (backend is fully usable headless,
this is just a convenience layer on top).

Run with:
    streamlit run streamlit_app/app.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import requests
import streamlit as st
from dotenv import find_dotenv, load_dotenv

# Loads the SAME .env the FastAPI backend uses (searches parent directories,
# so this works whether Streamlit is launched from the project root or from
# inside streamlit_app/). This is what lets the dashboard "just work" without
# retyping the admin key every time you open it.
load_dotenv(find_dotenv())

st.set_page_config(
    page_title="Dental Booking Agent — Dashboard",
    page_icon="🦷",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar: connection settings
# ---------------------------------------------------------------------------

if "backend_url" not in st.session_state:
    st.session_state.backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
if "admin_api_key" not in st.session_state:
    st.session_state.admin_api_key = os.getenv("ADMIN_API_KEY", "")
if "sim_call_id" not in st.session_state:
    st.session_state.sim_call_id = None
if "sim_history" not in st.session_state:
    st.session_state.sim_history = []

with st.sidebar:
    st.title("🦷 Booking Agent")
    st.caption("Dashboard for the FastAPI backend")

    if st.session_state.admin_api_key:
        st.success("Loaded backend URL and admin key from .env")
    else:
        st.warning("No ADMIN_API_KEY found in .env — set it there, or enter one below.")

    with st.expander("Advanced: override connection settings"):
        st.session_state.backend_url = st.text_input(
            "Backend URL", value=st.session_state.backend_url, help="e.g. http://localhost:8000 or your Railway URL"
        )
        st.session_state.admin_api_key = st.text_input(
            "Admin API key", value=st.session_state.admin_api_key, type="password"
        )

    if st.button("Test connection", use_container_width=True):
        try:
            r = requests.get(f"{st.session_state.backend_url}/health", timeout=5)
            if r.status_code == 200:
                st.success(f"Connected — {r.json()}")
            else:
                st.error(f"Backend responded with {r.status_code}")
        except requests.RequestException as e:
            st.error(f"Could not reach backend: {e}")

    st.divider()
    page = st.radio(
        "View",
        ["Overview", "Bookings", "Conversations", "Simulate a call"],
        label_visibility="collapsed",
    )


def admin_headers() -> dict:
    return {"x-admin-api-key": st.session_state.admin_api_key}


def backend(path: str) -> str:
    return f"{st.session_state.backend_url.rstrip('/')}{path}"


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

if page == "Overview":
    st.header("Overview")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Backend status")
        try:
            r = requests.get(backend("/"), timeout=5)
            if r.ok:
                data = r.json()
                st.metric("Clinic", data.get("clinic", "—"))
                st.metric("Environment", data.get("environment", "—"))
                st.success("Backend is reachable")
            else:
                st.error(f"Backend returned {r.status_code}")
        except requests.RequestException as e:
            st.error(f"Could not reach backend at {st.session_state.backend_url}: {e}")

    with col2:
        st.subheader("Quick stats")
        try:
            r = requests.get(backend("/admin/bookings"), headers=admin_headers(), timeout=10)
            if r.status_code == 200:
                bookings = r.json()["bookings"]
                st.metric("Total bookings", len(bookings))
                confirmed = sum(1 for b in bookings if b["status"] == "confirmed")
                st.metric("Confirmed bookings", confirmed)
            elif r.status_code == 401:
                st.warning("Enter a valid admin API key in the sidebar to see stats.")
            else:
                st.error(f"Unexpected response: {r.status_code}")
        except requests.RequestException as e:
            st.error(f"Could not reach backend: {e}")

    st.divider()
    st.markdown(
        """
        **What each tab does:**
        - **Bookings** — every confirmed appointment, pulled straight from Firestore via the admin API
        - **Conversations** — full turn-by-turn transcript + extracted slot data for any call
        - **Simulate a call** — a live chat-style tester that fires real webhook requests at your
          backend exactly like Vapi would, so you can test end-to-end without a phone call
        """
    )

# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

elif page == "Bookings":
    st.header("Bookings")

    clinic_filter = st.text_input("Filter by clinic_id (optional)", value="")
    if st.button("Refresh", type="primary"):
        st.rerun()

    try:
        params = {"clinic_id": clinic_filter} if clinic_filter else {}
        r = requests.get(backend("/admin/bookings"), headers=admin_headers(), params=params, timeout=10)
        if r.status_code == 401:
            st.warning("Enter a valid admin API key in the sidebar.")
        elif r.status_code != 200:
            st.error(f"Backend returned {r.status_code}: {r.text}")
        else:
            bookings = r.json()["bookings"]
            if not bookings:
                st.info("No bookings yet. Try the Simulate a call tab to create one.")
            else:
                for b in sorted(bookings, key=lambda x: x["start_time"], reverse=True):
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        with c1:
                            st.markdown(f"**{b['patient_name']}**")
                            st.caption(b["service"].replace("_", " ").title())
                        with c2:
                            start = datetime.fromisoformat(b["start_time"])
                            st.markdown(f"🗓️ {start.strftime('%b %d, %Y — %I:%M %p')}")
                            st.caption(f"call_id: `{b['call_id']}`")
                        with c3:
                            status_color = "🟢" if b["status"] == "confirmed" else "🔴"
                            st.markdown(f"{status_color} {b['status']}")
                        if b.get("calendar_event_id"):
                            st.caption(f"📅 Calendar event: {b['calendar_event_id']}")
                        if b.get("sms_sid"):
                            st.caption(f"📱 SMS sent: {b['sms_sid']}")
    except requests.RequestException as e:
        st.error(f"Could not reach backend: {e}")

# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

elif page == "Conversations":
    st.header("Conversations")

    try:
        r = requests.get(backend("/admin/conversations"), headers=admin_headers(), timeout=10)
        if r.status_code == 401:
            st.warning("Enter a valid admin API key in the sidebar.")
        elif r.status_code != 200:
            st.error(f"Backend returned {r.status_code}: {r.text}")
        else:
            conversations = r.json()["conversations"]
            if not conversations:
                st.info("No conversations logged yet.")
            else:
                options = {
                    f"{c['call_id']}  ·  stage: {c['stage']}": c["call_id"] for c in conversations
                }
                choice = st.selectbox("Pick a call", list(options.keys()))
                call_id = options[choice]

                detail_r = requests.get(
                    backend(f"/admin/conversations/{call_id}"), headers=admin_headers(), timeout=10
                )
                if detail_r.status_code == 200:
                    detail = detail_r.json()

                    col1, col2 = st.columns([1, 2])
                    with col1:
                        st.subheader("Extracted slots")
                        st.json(detail["slots"])
                        st.caption(f"Current stage: **{detail['stage']}**")

                    with col2:
                        st.subheader("Transcript")
                        for turn in detail["history"]:
                            role = "user" if turn["role"] == "user" else "assistant"
                            with st.chat_message(role):
                                st.write(turn["text"])
                                st.caption(turn.get("stage", ""))
                else:
                    st.error(f"Could not load conversation: {detail_r.status_code}")
    except requests.RequestException as e:
        st.error(f"Could not reach backend: {e}")

# ---------------------------------------------------------------------------
# Simulate a call
# ---------------------------------------------------------------------------

elif page == "Simulate a call":
    st.header("Simulate a call")
    st.caption(
        "Sends real `tool-calls` webhook requests to your backend, exactly the shape Vapi would send. "
        "Useful for testing the full flow without a real phone call."
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        phone_number = st.text_input(
            "Caller phone number (for SMS)", value="+91XXXXXXXXXX", help="Must be Twilio-verified on trial accounts"
        )
    with col2:
        if st.button("Start new call", use_container_width=True):
            st.session_state.sim_call_id = f"streamlit-{uuid.uuid4().hex[:8]}"
            st.session_state.sim_history = []
            st.rerun()

    if st.session_state.sim_call_id:
        st.info(f"Active call_id: `{st.session_state.sim_call_id}`")
    else:
        st.warning("Click 'Start new call' to begin.")

    for turn in st.session_state.sim_history:
        with st.chat_message(turn["role"]):
            st.write(turn["text"])

    utterance = st.chat_input("Say something as the caller...", disabled=not st.session_state.sim_call_id)

    if utterance and st.session_state.sim_call_id:
        st.session_state.sim_history.append({"role": "user", "text": utterance})

        payload = {
            "message": {
                "type": "tool-calls",
                "call": {"id": st.session_state.sim_call_id},
                "toolCalls": [
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": "process_turn",
                            "arguments": {
                                "utterance": utterance,
                                "caller_phone_number": phone_number,
                            },
                        },
                    }
                ],
            }
        }

        try:
            r = requests.post(backend("/webhook/vapi"), json=payload, timeout=30)
            if r.status_code == 200:
                reply = r.json()["results"][0]["result"]
                st.session_state.sim_history.append({"role": "assistant", "text": reply})
            else:
                st.session_state.sim_history.append(
                    {"role": "assistant", "text": f"[Error {r.status_code}] {r.text}"}
                )
        except requests.RequestException as e:
            st.session_state.sim_history.append({"role": "assistant", "text": f"[Connection error] {e}"})

        st.rerun()