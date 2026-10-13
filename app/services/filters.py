"""
Multi-facet order filtering service.
"""
from __future__ import annotations
import re
import logging
from typing import Any

log = logging.getLogger(__name__)


def apply_filter_criteria(orders: list[dict], criteria: dict) -> dict:
    """
    Filter orders by multi-facet criteria.

    criteria keys:
      search, customers, branches, sales_reps, weeks, statuses,
      invoice_month, invoice_year, has_email, has_sales_rep,
      value_min, value_max, page, page_size, sort, sort_dir
    """
    page = int(criteria.get("page", 1))
    page_size = int(criteria.get("page_size", 50))
    search = (criteria.get("search") or "").strip().lower()
    customers = criteria.get("customers") or []
    branches = criteria.get("branches") or []
    sales_reps = criteria.get("sales_reps") or []
    weeks = criteria.get("weeks") or []
    statuses = criteria.get("statuses") or []
    has_email = criteria.get("has_email")
    has_sales_rep = criteria.get("has_sales_rep")
    value_min = criteria.get("value_min")
    value_max = criteria.get("value_max")
    sort = criteria.get("sort", "customer_name")
    sort_dir = criteria.get("sort_dir", "asc")

    filtered = list(orders)

    if search:
        filtered = [o for o in filtered if (
            search in (o.get("customer_name") or "").lower() or
            search in (o.get("order_number") or "").lower() or
            search in (o.get("po_number") or "").lower() or
            search in (o.get("sales_rep") or "").lower() or
            search in (o.get("branch") or "").lower()
        )]

    if customers:
        cust_set = {c.lower() for c in customers}
        filtered = [o for o in filtered if (o.get("customer_name") or "").lower() in cust_set]

    if branches:
        br_set = {b.lower() for b in branches}
        filtered = [o for o in filtered if (o.get("branch") or "").lower() in br_set]

    if sales_reps:
        rep_set = {r.lower() for r in sales_reps}
        filtered = [o for o in filtered if (o.get("sales_rep") or "").lower() in rep_set]

    if weeks:
        week_set = {str(w).upper() for w in weeks}
        filtered = [o for o in filtered if (o.get("working_week") or "").upper() in week_set]

    if statuses:
        st_set = {s.lower() for s in statuses}
        filtered = [o for o in filtered if (o.get("order_status") or "pending").lower() in st_set]

    if has_email is not None:
        filtered = [o for o in filtered if bool(o.get("customer_email")) == has_email]

    if has_sales_rep is not None:
        filtered = [o for o in filtered if bool(o.get("sales_rep")) == has_sales_rep]

    if value_min is not None:
        try:
            vmin = float(value_min)
            filtered = [o for o in filtered if _parse_value(o.get("value")) >= vmin]
        except (TypeError, ValueError):
            pass

    if value_max is not None:
        try:
            vmax = float(value_max)
            filtered = [o for o in filtered if _parse_value(o.get("value")) <= vmax]
        except (TypeError, ValueError):
            pass

    # Sort
    reverse = sort_dir.lower() == "desc"
    try:
        if sort == "value":
            filtered.sort(key=lambda o: _parse_value(o.get("value")), reverse=reverse)
        else:
            filtered.sort(key=lambda o: str(o.get(sort) or "").lower(), reverse=reverse)
    except Exception as e:
        log.warning("Sort error: %s", e)

    total = len(filtered)

    # Pagination
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    # Facets for UI
    facets = _compute_facets(orders)

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "facets": facets,
    }


def _parse_value(raw) -> float:
    try:
        return float(str(raw or "0").replace(",", "").replace("₹", "").strip() or "0")
    except (ValueError, TypeError):
        return 0.0


def _compute_facets(orders: list[dict]) -> dict:
    """Compute available filter values from full order set."""
    customers = sorted({o.get("customer_name", "") for o in orders if o.get("customer_name")})
    branches = sorted({o.get("branch", "") for o in orders if o.get("branch")})
    sales_reps = sorted({o.get("sales_rep", "") for o in orders
                          if o.get("sales_rep") and o["sales_rep"] != "No Sales Credit"})
    weeks = sorted({o.get("working_week", "") for o in orders if o.get("working_week")})
    statuses = sorted({(o.get("order_status") or "pending") for o in orders})
    return {
        "customers": customers,
        "branches": branches,
        "sales_reps": sales_reps,
        "weeks": weeks,
        "statuses": statuses,
    }
