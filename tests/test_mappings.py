"""Tests for mapping CRUD (sales rep, branch, customer)."""
import pytest


def test_get_mappings_default(client):
    resp = client.get("/api/mappings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sales" in data
    assert "branch" in data
    assert "customer_emails" in data


def test_save_and_reload_mappings(client):
    payload = {
        "sales": {"Alice": "alice@example.com", "Bob": "bob@example.com"},
        "branch": {"HYD": "hyd@example.com"},
        "invoice": "invoice@example.com",
    }
    resp = client.post("/api/mappings", json=payload)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    resp2 = client.get("/api/mappings")
    data = resp2.get_json()
    assert data["sales"]["Alice"] == "alice@example.com"
    assert data["branch"]["HYD"] == "hyd@example.com"
    assert data["invoice"] == "invoice@example.com"


def test_save_customer_emails(client):
    payload = {
        "customer_emails": {
            "ACME Corp": "purchasing@acme.com",
            "Beta Ltd": "orders@beta.com",
        }
    }
    resp = client.post("/api/mappings", json=payload)
    assert resp.status_code == 200

    resp2 = client.get("/api/mappings")
    data = resp2.get_json()
    assert data["customer_emails"]["ACME Corp"] == "purchasing@acme.com"


def test_export_mappings(client):
    resp = client.get("/api/export_mappings")
    assert resp.status_code == 200
    assert resp.content_type == "application/json"
    import json
    data = json.loads(resp.data)
    assert "sales" in data


def test_partial_update_does_not_clobber(client):
    """Saving only 'invoice' should not overwrite existing 'sales'."""
    client.post("/api/mappings", json={
        "sales": {"Alice": "alice@example.com"},
    })
    client.post("/api/mappings", json={
        "invoice": "invoice@example.com",
    })
    resp = client.get("/api/mappings")
    data = resp.get_json()
    # sales should still be there
    assert data["sales"].get("Alice") == "alice@example.com"
