"""Tests for authentication."""
import json
import pytest


def test_login_success(client):
    resp = client.post("/login", json={"username": "testuser", "password": "testpass"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_login_wrong_password(client):
    resp = client.post("/login", json={"username": "testuser", "password": "wrong"})
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["success"] is False
    assert "AUTH_FAILED" in data["error"]["code"]


def test_login_wrong_user(client):
    resp = client.post("/login", json={"username": "nobody", "password": "testpass"})
    assert resp.status_code == 401


def test_logout(client):
    client.post("/login", json={"username": "testuser", "password": "testpass"})
    resp = client.post("/logout")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_readiness_endpoint(client):
    resp = client.get("/readiness")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "status" in data


def test_api_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("status") in ("ok", "db_error")
