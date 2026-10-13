"""
SMTP email service with error classification, retry, and connection reuse.
"""
from __future__ import annotations

import smtplib
import logging
import time
from enum import Enum
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
import requests as req_lib

log = logging.getLogger(__name__)


class SMTPErrorCode(Enum):
    AUTH_FAILED = "SMTP_AUTH_FAILED"
    CONNECTION_FAILED = "SMTP_CONNECTION_FAILED"
    TIMEOUT = "SMTP_TIMEOUT"
    TLS_FAILED = "SMTP_TLS_FAILED"
    INVALID_RECIPIENT = "INVALID_RECIPIENT"
    RATE_LIMITED = "RATE_LIMITED"
    TEMP_ERROR = "TEMPORARY_PROVIDER_ERROR"
    PERM_ERROR = "PERMANENT_PROVIDER_ERROR"
    CONFIG_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN_ERROR"


class EmailSendError(Exception):
    def __init__(self, code: SMTPErrorCode, message: str, retriable: bool = False):
        self.code = code
        self.message = message
        self.retriable = retriable
        super().__init__(f"[{code.value}] {message}")


def _classify_smtp_error(exc: Exception) -> tuple[SMTPErrorCode, bool]:
    """
    Returns (SMTPErrorCode, is_retriable).
    Auth failures must never be retried — they indicate a config problem.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SMTPErrorCode.AUTH_FAILED, False
    if isinstance(exc, smtplib.SMTPConnectError):
        return SMTPErrorCode.CONNECTION_FAILED, True
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return SMTPErrorCode.CONNECTION_FAILED, True
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return SMTPErrorCode.INVALID_RECIPIENT, False
    if isinstance(exc, TimeoutError):
        return SMTPErrorCode.TIMEOUT, True
    if isinstance(exc, smtplib.SMTPDataError):
        code = getattr(exc, "smtp_code", 0)
        if code and 400 <= code < 500:
            return SMTPErrorCode.TEMP_ERROR, True
        if code and 500 <= code < 600:
            return SMTPErrorCode.PERM_ERROR, False
        return SMTPErrorCode.UNKNOWN, True
    if isinstance(exc, smtplib.SMTPException):
        msg = str(exc).lower()
        if "rate" in msg or "too many" in msg:
            return SMTPErrorCode.RATE_LIMITED, True
        if "tls" in msg or "ssl" in msg:
            return SMTPErrorCode.TLS_FAILED, False
        return SMTPErrorCode.TEMP_ERROR, True
    if isinstance(exc, OSError):
        return SMTPErrorCode.CONNECTION_FAILED, True
    return SMTPErrorCode.UNKNOWN, True


_RETRY_DELAYS = [2, 5, 15]  # seconds between attempts


def send_via_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_use_tls: bool,
    username: str,
    password: str,
    from_addr: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    html_body: str,
    company: str,
    timeout: int = 30,
    max_retries: int = 3,
    smtp_conn=None,
) -> dict:
    """
    Send a single email via SMTP with retry logic.
    Returns {'ok': True} or raises EmailSendError.
    If smtp_conn is provided (an open smtplib.SMTP object), reuse it.
    """
    clean_password = password.replace(" ", "")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{company} <{from_addr}>"
    msg["To"] = ", ".join(to_list) if to_list else from_addr
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(html_body, "html"))
    all_rcpts = list(set(to_list + cc_list)) or [from_addr]

    last_exc = None
    for attempt in range(max_retries):
        try:
            if smtp_conn is not None:
                # Reuse provided connection
                smtp_conn.sendmail(from_addr, all_rcpts, msg.as_string())
                return {"ok": True, "attempt": attempt + 1}
            else:
                if smtp_use_tls:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as s:
                        s.ehlo()
                        s.starttls()
                        s.ehlo()
                        s.login(username, clean_password)
                        s.sendmail(from_addr, all_rcpts, msg.as_string())
                else:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout) as s:
                        s.ehlo()
                        s.login(username, clean_password)
                        s.sendmail(from_addr, all_rcpts, msg.as_string())
                return {"ok": True, "attempt": attempt + 1}
        except Exception as exc:
            last_exc = exc
            code, retriable = _classify_smtp_error(exc)
            log.warning("SMTP attempt %d/%d failed [%s]: %s", attempt + 1, max_retries, code.value, exc)

            if not retriable:
                raise EmailSendError(code, str(exc), retriable=False)

            if attempt < max_retries - 1:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                log.info("Retrying in %ds...", delay)
                time.sleep(delay)

    code, retriable = _classify_smtp_error(last_exc)
    raise EmailSendError(code, str(last_exc), retriable=retriable)


def send_via_n8n(
    webhook_url: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    html_body: str,
    customer_name: str,
    timeout: int = 30,
) -> dict:
    """Send email via n8n webhook."""
    from datetime import datetime
    payload = {
        "to": ", ".join(to_list),
        "cc": ", ".join(cc_list) if cc_list else "",
        "subject": subject,
        "html_body": html_body,
        "customer": customer_name,
        "sent_at": datetime.utcnow().isoformat(),
    }
    try:
        resp = req_lib.post(
            webhook_url, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return {"ok": True}
    except req_lib.exceptions.HTTPError as e:
        raise EmailSendError(SMTPErrorCode.PERM_ERROR, f"Webhook HTTP error: {e}", retriable=False)
    except req_lib.exceptions.Timeout:
        raise EmailSendError(SMTPErrorCode.TIMEOUT, "Webhook timed out", retriable=True)
    except req_lib.exceptions.ConnectionError as e:
        raise EmailSendError(SMTPErrorCode.CONNECTION_FAILED, f"Webhook connection failed: {e}", retriable=True)


def send_via_brevo(
    api_key: str,
    from_email: str,
    from_name: str,
    to_list: list[str],
    cc_list: list[str],
    subject: str,
    html_body: str,
    timeout: int = 30,
) -> dict:
    """
    Send email via Brevo (formerly Sendinblue) REST API.
    Uses HTTPS port 443 — works on Render free tier (no SMTP port blocking).
    Free tier allows 300 emails/day.
    Returns {'ok': True} or raises EmailSendError.
    """
    if not api_key:
        raise EmailSendError(
            SMTPErrorCode.CONFIG_ERROR,
            "Brevo API key not configured. Set BREVO_API_KEY environment variable.",
            retriable=False,
        )

    to_objs = [{"email": e.strip()} for e in to_list if e.strip()]
    cc_objs = [{"email": e.strip()} for e in cc_list if e.strip()]

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": to_objs,
        "subject": subject,
        "htmlContent": html_body,
    }
    if cc_objs:
        payload["cc"] = cc_objs

    try:
        resp = req_lib.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 429:
            raise EmailSendError(SMTPErrorCode.RATE_LIMITED, "Brevo daily/rate limit reached", retriable=False)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            retriable = resp.status_code >= 500
            raise EmailSendError(
                SMTPErrorCode.PERM_ERROR if not retriable else SMTPErrorCode.TEMP_ERROR,
                f"Brevo API error {resp.status_code}: {detail}",
                retriable=retriable,
            )
        return {"ok": True}
    except req_lib.exceptions.Timeout:
        raise EmailSendError(SMTPErrorCode.TIMEOUT, "Brevo API request timed out", retriable=True)
    except req_lib.exceptions.ConnectionError as e:
        raise EmailSendError(SMTPErrorCode.CONNECTION_FAILED, f"Brevo connection failed: {e}", retriable=True)
    except EmailSendError:
        raise
    except Exception as e:
        raise EmailSendError(SMTPErrorCode.UNKNOWN, f"Brevo unexpected error: {e}", retriable=True)


def open_smtp_connection(smtp_host, smtp_port, smtp_use_tls, username, password, timeout=30):
    """Open and authenticate an SMTP connection for batch reuse."""
    clean_password = password.replace(" ", "")
    if smtp_use_tls:
        s = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(username, clean_password)
    else:
        s = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
        s.ehlo()
        s.login(username, clean_password)
    return s
