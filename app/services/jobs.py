"""
Job creation, status, and idempotency service.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


def make_idempotency_key(
    customer: str,
    invoice_month: int,
    invoice_year: int,
    order_numbers: list[str],
    recipients: list[str],
) -> str:
    """Stable hash: if the same email was already sent, return the same key."""
    parts = [
        customer.strip().lower(),
        str(invoice_month),
        str(invoice_year),
        ",".join(sorted(str(o) for o in order_numbers)),
        ",".join(sorted(r.lower() for r in recipients)),
    ]
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def create_email_jobs(
    db_session,
    batch_id: str,
    jobs_data: list[dict],
) -> list[int]:
    """
    Create EmailJob records for a batch send.
    Skips jobs that already have the same idempotency_key and status 'done'.
    Returns list of created job IDs.
    """
    from app.models.email_job import EmailJob

    created_ids = []
    for jd in jobs_data:
        ikey = jd["idempotency_key"]
        existing = db_session.query(EmailJob).filter_by(idempotency_key=ikey).first()
        if existing and existing.status == "done":
            log.info("Skipping already-sent job for customer=%s key=%s", jd["customer"], ikey)
            continue
        if existing:
            # Reset failed jobs for retry
            existing.status = "pending"
            existing.error_code = None
            existing.error_message = None
            existing.batch_id = batch_id
            db_session.flush()
            created_ids.append(existing.id)
            continue

        job = EmailJob(
            idempotency_key=ikey,
            status="pending",
            customer=jd["customer"],
            recipients_json=json.dumps(jd.get("to", []), ensure_ascii=False),
            cc_json=json.dumps(jd.get("cc", []), ensure_ascii=False),
            subject=jd["subject"],
            body_html=jd["body_html"],
            invoice_month=jd.get("invoice_month"),
            invoice_year=jd.get("invoice_year"),
            batch_id=batch_id,
        )
        db_session.add(job)
        db_session.flush()
        created_ids.append(job.id)

    db_session.commit()
    return created_ids


def get_batch_status(db_session, batch_id: str) -> dict:
    """Return aggregate status for a batch of jobs."""
    from app.models.email_job import EmailJob

    jobs = db_session.query(EmailJob).filter_by(batch_id=batch_id).all()
    if not jobs:
        return {"found": False, "batch_id": batch_id}

    total = len(jobs)
    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1

    done = by_status.get("done", 0)
    failed = by_status.get("failed", 0)
    pending = by_status.get("pending", 0) + by_status.get("running", 0)

    return {
        "found": True,
        "batch_id": batch_id,
        "total": total,
        "done": done,
        "failed": failed,
        "pending": pending,
        "complete": pending == 0,
        "status_counts": by_status,
        "jobs": [j.to_dict() for j in jobs],
    }
