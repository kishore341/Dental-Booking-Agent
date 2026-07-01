from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_bookings_endpoint_requires_admin_key():
    response = client.get("/admin/bookings")
    assert response.status_code == 422  # missing required header


def test_bookings_endpoint_rejects_wrong_key():
    response = client.get("/admin/bookings", headers={"x-admin-api-key": "wrong"})
    assert response.status_code == 401


def test_bookings_endpoint_returns_data_with_correct_key():
    with patch("app.api.admin.list_bookings", return_value=[]):
        response = client.get("/admin/bookings", headers={"x-admin-api-key": "test-admin-key"})
    assert response.status_code == 200
    assert response.json() == {"count": 0, "bookings": []}


def test_conversation_not_found_returns_404():
    with patch("app.api.admin.get_conversation_state", return_value=None):
        response = client.get(
            "/admin/conversations/nonexistent", headers={"x-admin-api-key": "test-admin-key"}
        )
    assert response.status_code == 404
