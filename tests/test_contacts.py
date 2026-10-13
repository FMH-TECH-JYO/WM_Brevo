"""Tests for contact extraction engine."""
import io
import pytest
from app.services.contacts import (
    extract_from_csv_bytes,
    extract_from_excel_bytes,
    extract_from_txt_bytes,
    extract_contacts,
    _find_col,
    _extract_from_dataframe,
)
import pandas as pd


def test_forbes_marshall_csv_format(contacts_csv_bytes):
    """Test Forbes Marshall Google Contacts export format."""
    contacts = extract_from_csv_bytes(contacts_csv_bytes, "contacts.csv")
    assert len(contacts) >= 2
    emails = [c["email"] for c in contacts]
    assert "aruhal@forbesmarshall.com" in emails
    assert "psharma@forbesmarshall.com" in emails
    # Check name assembly from First + Last
    names = [c["name"] for c in contacts]
    assert any("Akshat" in n for n in names)
    assert any("Priya" in n for n in names)


def test_unusual_headers(contacts_unusual_bytes):
    """Column name normalization for non-standard headers."""
    contacts = extract_from_csv_bytes(contacts_unusual_bytes, "unusual.csv")
    assert len(contacts) >= 1
    emails = [c["email"] for c in contacts]
    assert "alice@example.com" in emails


def test_email_normalization():
    """Emails are lowercased."""
    data = pd.DataFrame({
        "E-mail 1 - Value": ["Test@Example.COM", "user@domain.org"],
        "First Name": ["Test", "User"],
        "Last Name": ["User", "Two"],
    })
    contacts = _extract_from_dataframe(data, "test.csv")
    assert all(c["email"] == c["email"].lower() for c in contacts)


def test_invalid_email_filtered():
    """Rows with invalid emails are excluded."""
    data = pd.DataFrame({
        "email": ["valid@example.com", "not-an-email", "", "also@valid.com"],
        "name": ["Alice", "Bob", "Carol", "Dave"],
    })
    contacts = _extract_from_dataframe(data, "test.csv")
    emails = [c["email"] for c in contacts]
    assert "valid@example.com" in emails
    assert "also@valid.com" in emails
    assert "not-an-email" not in emails


def test_extract_from_txt():
    txt = b"Alice alice@company.com\nBob bob@company.com"
    contacts = extract_from_txt_bytes(txt, "staff.txt")
    emails = [c["email"] for c in contacts]
    assert "alice@company.com" in emails
    assert "bob@company.com" in emails


def test_dedup_emails_in_txt():
    """Duplicate emails are deduplicated."""
    txt = b"alice@x.com\nalice@x.com\nbob@x.com"
    contacts = extract_from_txt_bytes(txt, "test.txt")
    emails = [c["email"] for c in contacts]
    assert emails.count("alice@x.com") == 1


def test_extract_contacts_dispatch_csv(contacts_csv_bytes):
    """extract_contacts dispatches correctly by extension."""
    contacts = extract_contacts(contacts_csv_bytes, "contacts.csv")
    assert len(contacts) >= 1


def test_extract_contacts_dispatch_txt():
    txt = b"test@example.com - Alice"
    contacts = extract_contacts(txt, "data.txt")
    assert len(contacts) == 1


def test_extract_contacts_unknown_type():
    """Unknown extension returns empty list without crashing."""
    contacts = extract_contacts(b"data", "file.xyz")
    assert contacts == []


def test_find_col_case_insensitive():
    """Column detection is case-insensitive via normalization."""
    cols = ["E-mail 1 - Value", "First Name", "Last Name"]
    result = _find_col(cols, ["e-mail 1 - value"])
    assert result == "E-mail 1 - Value"


def test_upload_contacts_api(client, contacts_csv_bytes):
    data = {
        "file": (io.BytesIO(contacts_csv_bytes), "contacts.csv"),
        "force": "false",
    }
    resp = client.post("/api/upload_contacts",
                       content_type="multipart/form-data", data=data)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["total_contacts"] >= 2
