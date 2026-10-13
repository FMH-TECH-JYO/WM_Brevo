"""
Background email worker.
Uses a thread pool with DB-backed job tracking (no global state).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def get_executor(max_workers: int = 4) -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None or _executor._shutdown:
            _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="email-worker")
    return _executor


def dispatch_batch(app, job_ids: list[int], cfg: dict):
    """
    Submit all job_ids for execution.
    Each job runs in a thread pool worker; progress written to DB.
    """
    executor = get_executor()
    for job_id in job_ids:
        executor.submit(_process_job, app, job_id, cfg)
    log.info("Dispatched %d email jobs", len(job_ids))


def _process_job(app, job_id: int, cfg: dict):
    """Worker function: process one EmailJob record."""
    from app.models.email_job import EmailJob, EmailAttempt
    from app.models.send_log import SendLog
    from app.services.email_service import (
        send_via_smtp, send_via_n8n, send_via_brevo, EmailSendError, SMTPErrorCode,
    )
    from app.extensions import db

    with app.app_context():
        job = db.session.get(EmailJob, job_id)
        if not job:
            log.error("Job %d not found", job_id)
            return

        if job.status == "done":
            log.info("Job %d already done, skipping", job_id)
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        db.session.commit()

        to_list = job.to_list
        cc_list = job.cc_list

        if not to_list:
            job.status = "failed"
            job.error_code = "NO_RECIPIENT"
            job.error_message = "No recipient email addresses"
            job.completed_at = datetime.utcnow()
            _log_send(db, job, "failed", "NO_RECIPIENT", "No recipient email addresses")
            db.session.commit()
            return

        mode = cfg.get("send_mode", "smtp")
        max_retries = int(cfg.get("max_retries", 3))
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                if mode == "n8n":
                    webhook = cfg.get("n8n_webhook", "").strip()
                    if not webhook:
                        raise EmailSendError(
                            SMTPErrorCode.CONFIG_ERROR, "n8n webhook URL not configured", retriable=False
                        )
                    send_via_n8n(
                        webhook, to_list, cc_list,
                        job.subject, job.body_html, job.customer,
                        timeout=int(cfg.get("email_timeout", 30)),
                    )
                elif mode == "brevo":
                    company = cfg.get("company", "FORBES MARSHALL HYD PVT LTD")
                    from_email = cfg.get("brevo_sender_email", cfg.get("smtp_username", cfg.get("gmail", "")))
                    send_via_brevo(
                        api_key=cfg.get("brevo_api_key", ""),
                        from_email=from_email,
                        from_name=company,
                        to_list=to_list,
                        cc_list=cc_list,
                        subject=job.subject,
                        html_body=job.body_html,
                        timeout=int(cfg.get("email_timeout", 30)),
                    )
                else:
                    send_via_smtp(
                        smtp_host=cfg.get("smtp_host", "smtp.gmail.com"),
                        smtp_port=int(cfg.get("smtp_port", 587)),
                        smtp_use_tls=cfg.get("smtp_use_tls", True),
                        username=cfg.get("smtp_username", cfg.get("gmail", "")),
                        password=cfg.get("smtp_password", cfg.get("password", "")),
                        from_addr=cfg.get("smtp_username", cfg.get("gmail", "")),
                        to_list=to_list,
                        cc_list=cc_list,
                        subject=job.subject,
                        html_body=job.body_html,
                        company=cfg.get("company", "FORBES MARSHALL HYD PVT LTD"),
                        timeout=int(cfg.get("email_timeout", 30)),
                        max_retries=1,  # outer loop handles retries
                    )

                # Success
                job.status = "done"
                job.attempt_count = attempt
                job.completed_at = datetime.utcnow()
                job.error_code = None
                job.error_message = None

                attempt_rec = EmailAttempt(
                    job_id=job.id, status="ok",
                    smtp_response="sent",
                )
                db.session.add(attempt_rec)
                _log_send(db, job, "sent", None, None)
                db.session.commit()
                log.info("Job %d sent OK to %s (attempt %d)", job_id, job.customer, attempt)
                return

            except EmailSendError as e:
                last_error = e
                job.attempt_count = attempt
                attempt_rec = EmailAttempt(
                    job_id=job.id, status="failed",
                    error_code=e.code.value,
                    error_message=e.message,
                )
                db.session.add(attempt_rec)
                db.session.commit()
                log.warning("Job %d attempt %d failed [%s]: %s", job_id, attempt, e.code.value, e.message)

                if not e.retriable:
                    break
                if attempt < max_retries:
                    delays = [2, 5, 15]
                    time.sleep(delays[min(attempt - 1, len(delays) - 1)])

            except Exception as exc:
                last_error = exc
                attempt_rec = EmailAttempt(
                    job_id=job.id, status="failed",
                    error_code="UNKNOWN_ERROR",
                    error_message=str(exc),
                )
                db.session.add(attempt_rec)
                db.session.commit()
                log.exception("Job %d unexpected error", job_id)
                break

        # All retries exhausted
        err_code = last_error.code.value if isinstance(last_error, EmailSendError) else "UNKNOWN_ERROR"
        err_msg = str(last_error)
        job.status = "failed"
        job.error_code = err_code
        job.error_message = err_msg
        job.completed_at = datetime.utcnow()
        _log_send(db, job, "failed", err_code, err_msg)
        db.session.commit()
        log.error("Job %d permanently failed: %s", job_id, err_msg)


def _log_send(db, job, status, error_code, error_message):
    from app.models.send_log import SendLog
    sl = SendLog(
        job_id=job.id,
        customer=job.customer,
        to_emails=", ".join(job.to_list),
        cc_emails=", ".join(job.cc_list),
        subject=job.subject,
        status=status,
        attempts=job.attempt_count or 1,
        error_code=error_code,
        error_message=error_message,
    )
    db.session.add(sl)
