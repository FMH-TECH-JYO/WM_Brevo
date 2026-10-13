"""Tests for email job creation, status, and idempotency."""
import pytest
from unittest.mock import patch, MagicMock


def test_create_jobs_basic(app, db):
    """Jobs are created with correct fields."""
    from app.services.jobs import create_email_jobs, get_batch_status
    from app.models.email_job import EmailJob

    jobs_data = [{
        "idempotency_key": "test-key-001",
        "customer": "ACME",
        "to": ["buyer@acme.com"],
        "cc": ["sales@fm.com"],
        "subject": "Order Readiness Update",
        "body_html": "<p>Hello</p>",
        "invoice_month": 9,
        "invoice_year": 2026,
    }]
    ids = create_email_jobs(db.session, "batch-001", jobs_data)
    assert len(ids) == 1

    job = db.session.get(EmailJob, ids[0])
    assert job.customer == "ACME"
    assert job.status == "pending"
    assert "buyer@acme.com" in job.to_list


def test_idempotency_skips_done_job(app, db):
    """A job with status 'done' and same idempotency key is skipped."""
    from app.services.jobs import create_email_jobs
    from app.models.email_job import EmailJob

    jobs_data = [{
        "idempotency_key": "idem-key-done",
        "customer": "Beta",
        "to": ["b@beta.com"],
        "cc": [],
        "subject": "S",
        "body_html": "B",
        "invoice_month": 9,
        "invoice_year": 2026,
    }]
    ids = create_email_jobs(db.session, "batch-002", jobs_data)
    job = db.session.get(EmailJob, ids[0])
    job.status = "done"
    db.session.commit()

    # Try creating same job again — should be skipped
    ids2 = create_email_jobs(db.session, "batch-003", jobs_data)
    assert ids2 == []


def test_idempotency_retries_failed_job(app, db):
    """A job with status 'failed' and same key is reset to pending for retry."""
    from app.services.jobs import create_email_jobs
    from app.models.email_job import EmailJob

    jobs_data = [{
        "idempotency_key": "idem-key-fail",
        "customer": "Gamma",
        "to": ["g@gamma.com"],
        "cc": [],
        "subject": "S",
        "body_html": "B",
        "invoice_month": 9,
        "invoice_year": 2026,
    }]
    ids = create_email_jobs(db.session, "batch-004", jobs_data)
    job = db.session.get(EmailJob, ids[0])
    job.status = "failed"
    db.session.commit()

    ids2 = create_email_jobs(db.session, "batch-005", jobs_data)
    assert len(ids2) == 1
    job2 = db.session.get(EmailJob, ids2[0])
    assert job2.status == "pending"


def test_get_batch_status(app, db):
    from app.services.jobs import create_email_jobs, get_batch_status

    jobs_data = [
        {"idempotency_key": f"bs-{i}", "customer": f"C{i}",
         "to": [f"c{i}@e.com"], "cc": [],
         "subject": "S", "body_html": "B",
         "invoice_month": 9, "invoice_year": 2026}
        for i in range(3)
    ]
    create_email_jobs(db.session, "batch-status-test", jobs_data)
    status = get_batch_status(db.session, "batch-status-test")
    assert status["found"] is True
    assert status["total"] == 3
    assert status["pending"] == 3
    assert status["complete"] is False


def test_get_batch_status_not_found(app, db):
    from app.services.jobs import get_batch_status
    status = get_batch_status(db.session, "nonexistent-batch")
    assert status["found"] is False


def test_send_start_no_orders(client, db):
    """Explicitly reset state and verify 400 when no orders loaded."""
    # Reset shared STATE to simulate no upload
    from app.api.routes import STATE
    STATE["filtered_orders"] = []
    STATE["grouped"] = {}
    STATE["all_orders"] = []
    resp = client.post("/api/send/start", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert "NO_ORDERS" in body["error"]["code"]


def test_send_status_not_found(client, db):
    resp = client.get("/api/send/status/nonexistent-batch")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
