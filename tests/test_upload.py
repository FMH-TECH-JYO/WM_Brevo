"""Tests for Excel/CSV file upload."""
import io
import pytest


def test_upload_no_file(client):
    resp = client.post("/api/upload")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_upload_valid_excel(client, sample_invoice_bytes):
    data = {"file": (io.BytesIO(sample_invoice_bytes), "sample_invoice.xlsx")}
    resp = client.post("/api/upload", content_type="multipart/form-data", data=data)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["imported"] > 0


def test_upload_bad_extension(client):
    data = {"file": (io.BytesIO(b"hello world"), "bad.exe")}
    resp = client.post("/api/upload", content_type="multipart/form-data", data=data)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert "INVALID_FILE" in body["error"]["code"]


def test_upload_empty_file(client):
    data = {"file": (io.BytesIO(b""), "empty.csv")}
    resp = client.post("/api/upload", content_type="multipart/form-data", data=data)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False


def test_upload_with_month(client, sample_invoice_bytes):
    data = {
        "file": (io.BytesIO(sample_invoice_bytes), "sample.xlsx"),
        "month": "9",
        "year": "2026",
    }
    resp = client.post("/api/upload", content_type="multipart/form-data", data=data)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True


def test_upload_sets_month_auto(client, sample_invoice_bytes):
    """The SEP INV sheet should auto-detect September."""
    data = {"file": (io.BytesIO(sample_invoice_bytes), "sample_invoice.xlsx")}
    resp = client.post("/api/upload", content_type="multipart/form-data", data=data)
    body = resp.get_json()
    assert body["success"] is True
    # Should detect month 9 (September) from the "SEP INV" sheet name
    assert body["data"]["month"] == 9


def test_set_month(client, sample_invoice_bytes):
    # Upload first
    data = {"file": (io.BytesIO(sample_invoice_bytes), "sample.xlsx")}
    client.post("/api/upload", content_type="multipart/form-data", data=data)

    resp = client.post("/api/set_month", json={"month": 9, "year": 2026})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "filtered" in body["data"]


def test_set_month_invalid(client):
    resp = client.post("/api/set_month", json={"month": 13, "year": 2026})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False


def test_orders_endpoint(client, sample_invoice_bytes):
    data = {"file": (io.BytesIO(sample_invoice_bytes), "sample.xlsx"),
            "month": "9", "year": "2026"}
    client.post("/api/upload", content_type="multipart/form-data", data=data)

    resp = client.get("/api/orders")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
