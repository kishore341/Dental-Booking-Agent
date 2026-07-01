"""Initializes the Firebase Admin SDK exactly once per process.

Supports two ways of supplying credentials:
1. A path to a service-account JSON file (local dev) -- FIREBASE_CREDENTIALS_PATH
2. The raw JSON contents in an env var (Railway/Render deploy, where you
   can't easily ship a secret file) -- FIREBASE_CREDENTIALS_JSON
"""

from __future__ import annotations

import json
import logging
import tempfile

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import get_settings

logger = logging.getLogger(__name__)

_db = None


def get_firestore_client():
    global _db
    if _db is not None:
        return _db

    settings = get_settings()

    if not firebase_admin._apps:
        if settings.firebase_credentials_json:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                f.write(settings.firebase_credentials_json)
                cred_path = f.name
        else:
            cred_path = settings.firebase_credentials_path

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logger.info("firebase_admin_initialized")

    _db = firestore.client()
    return _db
