"""Tests for recipient resolution service."""
import pytest
from app.services.recipients import resolve_recipients, _parse_emails


def test_parse_emails_valid():
    result = _parse_emails("a@b.com, c@d.org, bad-email")
    assert "a@b.com" in result
    assert "c@d.org" in result
    assert "bad-email" not in result


def test_parse_emails_empty():
    assert _parse_emails("") == []
    assert _parse_emails(None) == []


def test_override_takes_priority():
    """Explicit override is used over mapping and excel data."""
    mappings = {
        "customer_emails": {"ACME": "stored@acme.com"},
        "sales": {}, "branch": {}, "invoice": "",
    }
    orders = [{"customer_email": "excel@acme.com", "sales_rep": "", "branch": ""}]
    result = resolve_recipients("ACME", orders, mappings, "override@acme.com")
    assert result["to"] == ["override@acme.com"]


def test_stored_mapping_second_priority():
    """Stored mapping used when no override."""
    mappings = {
        "customer_emails": {"ACME": "stored@acme.com"},
        "sales": {}, "branch": {}, "invoice": "",
    }
    orders = [{"customer_email": "excel@acme.com", "sales_rep": "", "branch": ""}]
    result = resolve_recipients("ACME", orders, mappings, None)
    assert result["to"] == ["stored@acme.com"]


def test_excel_data_third_priority():
    """Excel email used when no override and no stored mapping."""
    mappings = {
        "customer_emails": {},
        "sales": {}, "branch": {}, "invoice": "",
    }
    orders = [{"customer_email": "excel@acme.com", "sales_rep": "", "branch": ""}]
    result = resolve_recipients("ACME", orders, mappings, None)
    assert result["to"] == ["excel@acme.com"]


def test_no_email_error():
    """Error when no email found anywhere."""
    mappings = {"customer_emails": {}, "sales": {}, "branch": {}, "invoice": ""}
    orders = [{"customer_email": "", "sales_rep": "", "branch": ""}]
    result = resolve_recipients("Unknown", orders, mappings, None)
    assert result["to"] == []
    assert any("Unknown" in e for e in result["errors"])


def test_cc_includes_sales_and_branch():
    mappings = {
        "customer_emails": {"ACME": "customer@acme.com"},
        "sales": {"Alice": "alice@fm.com"},
        "branch": {"HYD": "hyd@fm.com"},
        "invoice": "invoice@fm.com",
    }
    orders = [{"customer_email": "", "sales_rep": "Alice", "branch": "HYD"}]
    result = resolve_recipients("ACME", orders, mappings, None)
    assert "alice@fm.com" in result["cc"]
    assert "hyd@fm.com" in result["cc"]
    assert "invoice@fm.com" in result["cc"]


def test_cc_deduplication():
    """Same email should not appear twice in CC."""
    mappings = {
        "customer_emails": {},
        "sales": {"Alice": "shared@fm.com"},
        "branch": {"HYD": "shared@fm.com"},
        "invoice": "",
    }
    orders = [{"customer_email": "c@x.com", "sales_rep": "Alice", "branch": "HYD"}]
    result = resolve_recipients("ACME", orders, mappings, None)
    assert result["cc"].count("shared@fm.com") == 1


def test_cc_does_not_duplicate_to():
    """A CC email that matches a TO email is removed from CC."""
    mappings = {
        "customer_emails": {"ACME": "same@acme.com"},
        "sales": {"Alice": "same@acme.com"},
        "branch": {}, "invoice": "",
    }
    orders = [{"customer_email": "", "sales_rep": "Alice", "branch": ""}]
    result = resolve_recipients("ACME", orders, mappings, None)
    assert "same@acme.com" in result["to"]
    assert "same@acme.com" not in result["cc"]


def test_invalid_override_email_warns():
    mappings = {"customer_emails": {}, "sales": {}, "branch": {}, "invoice": ""}
    orders = [{"customer_email": "", "sales_rep": "", "branch": ""}]
    result = resolve_recipients("ACME", orders, mappings, "not-an-email")
    assert result["to"] == []
    assert result["warnings"]
