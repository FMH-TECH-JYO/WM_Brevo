"""Tests for email service (mocked SMTP)."""
import pytest
import smtplib
from unittest.mock import patch, MagicMock, call

from app.services.email_service import (
    send_via_smtp,
    send_via_n8n,
    EmailSendError,
    SMTPErrorCode,
    _classify_smtp_error,
)


def test_classify_auth_error():
    code, retriable = _classify_smtp_error(smtplib.SMTPAuthenticationError(535, b"Bad auth"))
    assert code == SMTPErrorCode.AUTH_FAILED
    assert retriable is False


def test_classify_connect_error():
    code, retriable = _classify_smtp_error(smtplib.SMTPConnectError(421, b"Connect failed"))
    assert code == SMTPErrorCode.CONNECTION_FAILED
    assert retriable is True


def test_classify_recipient_refused():
    code, retriable = _classify_smtp_error(smtplib.SMTPRecipientsRefused({}))
    assert code == SMTPErrorCode.INVALID_RECIPIENT
    assert retriable is False


def test_classify_timeout():
    code, retriable = _classify_smtp_error(TimeoutError())
    assert code == SMTPErrorCode.TIMEOUT
    assert retriable is True


def test_send_via_smtp_success():
    mock_smtp = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp.return_value.__enter__ = lambda s: mock_smtp_instance
    mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=mock_smtp.return_value):
        result = send_via_smtp(
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_use_tls=True,
            username="test@gmail.com",
            password="apppass",
            from_addr="test@gmail.com",
            to_list=["to@example.com"],
            cc_list=[],
            subject="Test",
            html_body="<p>Hello</p>",
            company="FM",
            max_retries=1,
        )
    assert result["ok"] is True


def test_send_via_smtp_auth_failure_no_retry():
    """Auth failure must NOT be retried."""
    call_count = 0

    def fake_smtp(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"Bad auth")

    with patch("smtplib.SMTP", side_effect=fake_smtp):
        with pytest.raises(EmailSendError) as exc_info:
            send_via_smtp(
                smtp_host="smtp.gmail.com", smtp_port=587, smtp_use_tls=True,
                username="u", password="p", from_addr="u@g.com",
                to_list=["to@e.com"], cc_list=[],
                subject="S", html_body="B", company="C",
                max_retries=3,
            )
    assert exc_info.value.code == SMTPErrorCode.AUTH_FAILED
    assert exc_info.value.retriable is False


def test_send_via_smtp_transient_retry():
    """Connection error is retried up to max_retries."""
    call_count = [0]

    def fake_smtp(*args, **kwargs):
        call_count[0] += 1
        raise smtplib.SMTPConnectError(421, b"Temp fail")

    with patch("smtplib.SMTP", side_effect=fake_smtp), \
         patch("time.sleep"):
        with pytest.raises(EmailSendError) as exc_info:
            send_via_smtp(
                smtp_host="h", smtp_port=587, smtp_use_tls=True,
                username="u", password="p", from_addr="u@g.com",
                to_list=["to@e.com"], cc_list=[],
                subject="S", html_body="B", company="C",
                max_retries=3,
            )
    assert call_count[0] == 3
    assert exc_info.value.code == SMTPErrorCode.CONNECTION_FAILED


def test_send_via_n8n_success():
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()
        result = send_via_n8n(
            "https://n8n.example.com/webhook/test",
            ["to@e.com"], [],
            "Subject", "<p>body</p>", "ACME",
        )
    assert result["ok"] is True
    mock_post.assert_called_once()


def test_send_via_n8n_http_error():
    import requests
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_post.return_value = mock_resp
        with pytest.raises(EmailSendError) as exc_info:
            send_via_n8n("https://wh.example.com", ["t@e.com"], [], "S", "B", "C")
    assert exc_info.value.code == SMTPErrorCode.PERM_ERROR


def test_idempotency_key_stable():
    from app.services.jobs import make_idempotency_key
    key1 = make_idempotency_key("ACME", 9, 2026, ["ORD1", "ORD2"], ["a@b.com"])
    key2 = make_idempotency_key("ACME", 9, 2026, ["ORD2", "ORD1"], ["A@B.COM"])
    assert key1 == key2  # order and case insensitive


def test_idempotency_key_different_month():
    from app.services.jobs import make_idempotency_key
    key1 = make_idempotency_key("ACME", 9, 2026, ["ORD1"], ["a@b.com"])
    key2 = make_idempotency_key("ACME", 10, 2026, ["ORD1"], ["a@b.com"])
    assert key1 != key2
