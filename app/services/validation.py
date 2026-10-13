"""Input validation helpers."""
import re
import os

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".docx", ".txt", ".pdf"}


def is_valid_email(email: str) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


def validate_emails(raw: str) -> list[str]:
    """Parse a comma-separated email string and return only valid addresses."""
    parts = [e.strip() for e in raw.split(",") if e.strip()]
    return [e for e in parts if is_valid_email(e)]


def validate_upload_file(filename: str, file_bytes: bytes, max_mb: int = 50) -> list[str]:
    errors = []
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        errors.append(f"File type '{ext}' not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    max_bytes = max_mb * 1024 * 1024
    if len(file_bytes) > max_bytes:
        errors.append(f"File size {len(file_bytes)//1024//1024}MB exceeds {max_mb}MB limit")
    if len(file_bytes) == 0:
        errors.append("Empty file uploaded")
    return errors


def validate_month_year(month, year) -> list[str]:
    errors = []
    try:
        month = int(month)
        year = int(year)
    except (TypeError, ValueError):
        errors.append("month and year must be integers")
        return errors
    if not (1 <= month <= 12):
        errors.append("month must be between 1 and 12")
    if not (2000 <= year <= 2100):
        errors.append("year must be between 2000 and 2100")
    return errors
