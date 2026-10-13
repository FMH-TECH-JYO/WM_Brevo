"""
Single authoritative recipient resolver.
Used by preview, send, validation, test email, and logs.
Priority: explicit mapping > imported contact > excel data.
"""
from __future__ import annotations

import re
import logging
from typing import Any

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _parse_emails(raw: str) -> list[str]:
    """Split a comma-separated email string into a list of validated emails."""
    if not raw:
        return []
    return [e.strip() for e in raw.split(",")
            if e.strip() and _EMAIL_RE.match(e.strip())]


def resolve_recipients(
    customer: str,
    orders: list[dict],
    mappings: dict,
    override_customer_emails: str | None = None,
) -> dict:
    """
    Returns:
        {
            'to': [validated emails],
            'cc': [validated emails],
            'warnings': [...],
            'errors': [...],
            'sources': {email: source_description}
        }

    Priority for TO:
      1. override_customer_emails (explicit override passed by caller)
      2. customer_emails mapping (DB / stored mapping)
      3. Excel-provided customer_email on orders

    CC always includes:
      - sales rep email(s) from sales mapping
      - branch email(s) from branch mapping
      - invoice team emails
    """
    warnings = []
    errors = []
    sources = {}

    sales_map = mappings.get("sales", {})
    branch_map = mappings.get("branch", {})
    invoice_em = mappings.get("invoice", "")
    cust_email_map = mappings.get("customer_emails", {})

    to_list: list[str] = []

    # Priority 1: explicit override
    if override_customer_emails:
        parsed = _parse_emails(override_customer_emails)
        to_list.extend(parsed)
        for e in parsed:
            sources[e] = "explicit override"
        if not parsed:
            warnings.append(f"Override email for '{customer}' could not be parsed: {override_customer_emails!r}")

    # Priority 2: stored mapping
    if not to_list and cust_email_map.get(customer):
        parsed = _parse_emails(cust_email_map[customer])
        to_list.extend(parsed)
        for e in parsed:
            sources[e] = "customer email mapping"

    # Priority 3: Excel data from any of this customer's orders
    if not to_list:
        for o in orders:
            excel_email = o.get("customer_email", "")
            if excel_email:
                parsed = _parse_emails(excel_email)
                if parsed:
                    to_list.extend(parsed)
                    for e in parsed:
                        sources[e] = "excel upload data"
                    break

    if not to_list:
        errors.append(f"No email found for customer '{customer}'")

    # CC list
    cc_list: list[str] = []

    # Sales rep(s) from first order with a sales rep
    first_order = orders[0] if orders else {}
    rep_name = first_order.get("sales_rep", "")
    if rep_name:
        rep_emails = _parse_emails(sales_map.get(rep_name, ""))
        cc_list.extend(rep_emails)
        for e in rep_emails:
            sources[e] = f"sales rep mapping ({rep_name})"
        if not rep_emails and rep_name:
            warnings.append(f"No email mapped for sales rep '{rep_name}'")

    # Branch from first order
    branch = first_order.get("branch", "")
    if branch:
        br_emails = _parse_emails(branch_map.get(branch, ""))
        cc_list.extend(br_emails)
        for e in br_emails:
            sources[e] = f"branch mapping ({branch})"

    # Invoice team
    inv_emails = _parse_emails(invoice_em if isinstance(invoice_em, str) else "")
    cc_list.extend(inv_emails)
    for e in inv_emails:
        sources[e] = "invoice team"

    # Deduplicate CC (remove any that are already in TO)
    to_set = set(to_list)
    seen_cc: set[str] = set()
    cc_deduped = []
    for e in cc_list:
        if e not in seen_cc and e not in to_set:
            seen_cc.add(e)
            cc_deduped.append(e)

    return {
        "to": to_list,
        "cc": cc_deduped,
        "warnings": warnings,
        "errors": errors,
        "sources": sources,
    }
