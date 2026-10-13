"""Tests for multi-facet filtering."""
import pytest
from app.services.filters import apply_filter_criteria, _compute_facets, _parse_value


SAMPLE_ORDERS = [
    {"customer_name": "ACME", "order_number": "ORD001", "branch": "HYD",
     "sales_rep": "Alice", "working_week": "WEEK1", "order_status": "pending",
     "value": "50000", "customer_email": "acme@e.com", "po_number": "PO1"},
    {"customer_name": "ACME", "order_number": "ORD002", "branch": "HYD",
     "sales_rep": "Alice", "working_week": "WEEK2", "order_status": "pending",
     "value": "75000", "customer_email": "acme@e.com", "po_number": ""},
    {"customer_name": "Beta", "order_number": "ORD003", "branch": "Pune",
     "sales_rep": "Bob", "working_week": "WEEK1", "order_status": "ready",
     "value": "30000", "customer_email": "beta@e.com", "po_number": "PO3"},
    {"customer_name": "Gamma", "order_number": "ORD004", "branch": "HYD",
     "sales_rep": "", "working_week": None, "order_status": "pending",
     "value": "0", "customer_email": "", "po_number": ""},
]


def test_no_filter_returns_all():
    result = apply_filter_criteria(SAMPLE_ORDERS, {})
    assert result["total"] == 4


def test_search_by_customer():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"search": "acme"})
    assert result["total"] == 2
    assert all(o["customer_name"] == "ACME" for o in result["items"])


def test_filter_by_branch():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"branches": ["HYD"]})
    assert result["total"] == 3  # ACME x2 + Gamma


def test_filter_by_sales_rep():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"sales_reps": ["Alice"]})
    assert result["total"] == 2


def test_filter_by_week():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"weeks": ["WEEK1"]})
    assert result["total"] == 2


def test_filter_by_status():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"statuses": ["ready"]})
    assert result["total"] == 1
    assert result["items"][0]["customer_name"] == "Beta"


def test_filter_has_email_true():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"has_email": True})
    assert result["total"] == 3


def test_filter_has_email_false():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"has_email": False})
    assert result["total"] == 1
    assert result["items"][0]["customer_name"] == "Gamma"


def test_filter_has_sales_rep_false():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"has_sales_rep": False})
    assert result["total"] == 1


def test_value_min():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"value_min": 40000})
    assert all(_parse_value(o["value"]) >= 40000 for o in result["items"])


def test_value_max():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"value_max": 50000})
    assert all(_parse_value(o["value"]) <= 50000 for o in result["items"])


def test_pagination():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"page": 1, "page_size": 2})
    assert len(result["items"]) == 2
    assert result["total"] == 4
    assert result["pages"] == 2

    result2 = apply_filter_criteria(SAMPLE_ORDERS, {"page": 2, "page_size": 2})
    assert len(result2["items"]) == 2


def test_sort_by_value_desc():
    result = apply_filter_criteria(SAMPLE_ORDERS, {"sort": "value", "sort_dir": "desc"})
    vals = [_parse_value(o["value"]) for o in result["items"]]
    assert vals == sorted(vals, reverse=True)


def test_facets_include_all_values():
    result = apply_filter_criteria(SAMPLE_ORDERS, {})
    facets = result["facets"]
    assert "ACME" in facets["customers"]
    assert "Beta" in facets["customers"]
    assert "HYD" in facets["branches"]
    assert "Pune" in facets["branches"]
    assert "Alice" in facets["sales_reps"]


def test_combined_filters():
    result = apply_filter_criteria(SAMPLE_ORDERS, {
        "branches": ["HYD"],
        "sales_reps": ["Alice"],
    })
    assert result["total"] == 2


def test_orders_filter_api(client, sample_invoice_bytes):
    import io
    data = {"file": (io.BytesIO(sample_invoice_bytes), "sample.xlsx"),
            "month": "9", "year": "2026"}
    client.post("/api/upload", content_type="multipart/form-data", data=data)

    resp = client.post("/api/orders/filter", json={"page": 1, "page_size": 50})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "items" in body["data"]
    assert "facets" in body["data"]
