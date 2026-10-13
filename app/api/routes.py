"""
All API endpoints for the FM Invoice Readiness Mailer.
"""
from __future__ import annotations

import io
import os
import json
import uuid
import logging
import calendar
import random
import html as _html
from collections import Counter
from datetime import datetime, date

from flask import request, jsonify, current_app, session, g

from app.api import api_bp
from app.extensions import db

# Import excel_ingest (kept as a module-level import for reuse)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import excel_ingest as EI

from app.services.validation import validate_upload_file, validate_month_year, validate_emails
from app.services.recipients import resolve_recipients
from app.services.filters import apply_filter_criteria
from app.services.contacts import extract_contacts
from app.services.mappings import (
    load_mappings_legacy, save_mappings_legacy,
    upsert_customer_email, bulk_upsert_customer_emails,
    get_all_customer_emails,
)
from app.services.jobs import make_idempotency_key, create_email_jobs, get_batch_status
from app.workers.email_worker import dispatch_batch

log = logging.getLogger(__name__)

MONTHS_LONG = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
MONTHS_SHORT = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _ok(data=None, **kwargs):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)


def _err(code: str, message: str, details=None, http_status: int = 400):
    payload = {"success": False, "error": {"code": code, "message": message, "details": details}}
    return jsonify(payload), http_status


# ── In-memory state (per-process, not per-worker) ────────────────────────────
# Retained for backward compat with the single-worker deployment model.
# The send jobs themselves are DB-backed; this STATE is for the active upload.
STATE: dict = {
    "all_orders": [],
    "filtered_orders": [],
    "grouped": {},
    "sel_month": None,
    "sel_year": None,
    "import_diag": None,
    "filter_diag": None,
    "last_filename": "",
    "last_file_bytes": None,
    "column_overrides": {},
    "mapping_confirmed": False,
}


def _get_db_engine():
    from app.extensions import db as _db
    return _db.engine


# ── Calendar / date helpers ───────────────────────────────────────────────────
def _calc_ready_date(week_num: int, month: int, year: int):
    if not week_num or week_num < 1:
        return None
    days_in_month = calendar.monthrange(year, month)[1]
    if week_num <= 4:
        s = 7 * (week_num - 1) + 1
        e = min(7 * week_num, days_in_month)
        candidate_days = [date(year, month, d) for d in range(s, e + 1)]
    else:
        next_month = 1 if month == 12 else month + 1
        next_year = year + 1 if month == 12 else year
        candidate_days = [date(year, month, d) for d in range(29, days_in_month + 1)]
        candidate_days += [date(next_year, next_month, d) for d in range(1, 8)]
    weekday_candidates = [d for d in candidate_days if d.weekday() < 5]
    pool = weekday_candidates or candidate_days
    return random.choice(pool) if pool else None


def _fmt_date_long(d):
    if not d:
        return "-"
    if isinstance(d, str):
        return d
    return f"{d.day} {MONTHS_LONG[d.month - 1]} {d.year}"


def _fmt_value(val_str):
    try:
        v = float(str(val_str).replace(",", ""))
        return "₹{:,.0f}".format(v)
    except Exception:
        return val_str or "—"


# ── Filter application ────────────────────────────────────────────────────────
def _apply_filter():
    month = STATE["sel_month"]
    year = STATE["sel_year"]
    filter_reasons = Counter()
    filtered = []

    if not month or not year:
        STATE["filtered_orders"] = []
        STATE["grouped"] = {}
        STATE["filter_diag"] = {
            "reasons": {"No invoice month/year selected": len(STATE["all_orders"])},
            "total": len(STATE["all_orders"]),
            "excluded": len(STATE["all_orders"]),
            "included": 0,
        }
        return

    for o in STATE["all_orders"]:
        cw_raw = o.get("completion_week", "")
        pw_raw = o.get("planned_week", "")
        inv_raw = o.get("inv_month", "")
        combined_upper = " ".join(str(x).upper() for x in (cw_raw, pw_raw, inv_raw))
        if "HOLD" in combined_upper:
            filter_reasons["On hold"] += 1
            continue

        order_status = "pending"
        rd = None
        rd_long = "-"
        included = False
        reason = None

        cw_week = EI.normalise_week(cw_raw)
        if cw_week is not None:
            inv_mo, _inv_yr = EI.extract_month_year(inv_raw)
            if inv_mo and inv_mo != month:
                reason = "Invoice month mismatch"
            else:
                rd = _calc_ready_date(cw_week, month, year)
                rd_long = _fmt_date_long(rd)
                included = True
        else:
            if EI.is_ready_flag(cw_raw) or EI.is_ready_text(cw_raw):
                order_status = "ready"
            inv_mo, inv_yr = EI.extract_month_year(inv_raw)
            if inv_mo is None:
                embedded = EI.extract_month_abbr_from_text(inv_raw)
                if embedded:
                    inv_mo = MONTHS_SHORT.index(embedded) + 1
            if inv_mo is not None and (inv_mo != month or
                                        (inv_yr is not None and inv_yr != year)):
                reason = "Invoice month mismatch"
            else:
                included = True

        if not included:
            filter_reasons[reason or "Excluded"] += 1
            continue

        o2 = dict(o)
        o2["working_week"] = f"WEEK{cw_week}" if cw_week is not None else None
        o2["working_week_num"] = cw_week
        if o.get("_date_overridden"):
            # User manually set this date — preserve it, don't recalculate
            o2["ready_date"] = o.get("ready_date")
            # ready_date_long was computed and stored when the user saved the date;
            # fall back to parsing the YYYY-MM-DD string into a date object so
            # _fmt_date_long returns "14 September 2026" not "2026-09-14".
            raw_rd = o.get("ready_date")
            if o.get("ready_date_long"):
                o2["ready_date_long"] = o["ready_date_long"]
            elif raw_rd:
                try:
                    y, m, day = raw_rd.split("-")
                    o2["ready_date_long"] = _fmt_date_long(date(int(y), int(m), int(day)))
                except Exception:
                    o2["ready_date_long"] = raw_rd
            else:
                o2["ready_date_long"] = "-"
            o2["order_status"] = o.get("order_status", order_status)
        else:
            o2["ready_date"] = rd.isoformat() if rd else None
            o2["ready_date_long"] = rd_long
            o2["order_status"] = order_status
        filtered.append(o2)

    STATE["filtered_orders"] = filtered
    grouped = {}
    for o in filtered:
        k = o["customer_name"]
        if k not in grouped:
            grouped[k] = {"customer_name": k, "orders": []}
        grouped[k]["orders"].append(o)
    STATE["grouped"] = grouped
    STATE["filter_diag"] = {
        "reasons": dict(filter_reasons),
        "total": len(STATE["all_orders"]),
        "excluded": sum(filter_reasons.values()),
        "included": len(filtered),
    }


def _diagnostics_summary():
    diag = STATE.get("import_diag") or {}
    filt = STATE.get("filter_diag") or {}
    combined = Counter()
    combined.update(diag.get("skip_reason_counts", {}))
    combined.update(filt.get("reasons", {}))
    loaded = STATE.get("filtered_orders", [])
    no_cw = sum(1 for o in loaded if EI.normalise_week(o.get("completion_week", "")) is None)
    reports = diag.get("sheet_reports", [])
    used_reports = [r for r in reports if r.get("used")]
    warnings = []
    for r in used_reports:
        for missing in r.get("missing_required_columns", []):
            warnings.append(f"Sheet '{r['sheet']}': no {missing} column identified")
    return {
        "file_name": STATE.get("last_filename", ""),
        "excel_rows_detected": diag.get("total_rows", 0),
        "rows_imported_stage1": diag.get("imported", 0),
        "rows_skipped_stage1": len(diag.get("skipped_rows", [])),
        "rows_included_final": filt.get("included", 0),
        "rows_excluded_stage2": filt.get("excluded", 0),
        "orders_without_completion_week": no_cw,
        "duplicates_dropped": diag.get("skip_reason_counts", {}).get(
            "Duplicate row (identical to an earlier row)", 0),
        "repeated_order_numbers": sum(
            1 for o in STATE.get("all_orders", []) if o.get("_repeated_order_number")),
        "warnings": warnings,
        "reasons": dict(combined),
        "sheets_used": diag.get("sheets_used", []),
        "sheets_skipped": diag.get("sheets_skipped", []),
        "sheet_reports": reports,
        "month_detection": diag.get("month_detection", {}),
        "mapping": _mapping_info(),
    }


MAPPABLE_FIELDS = [
    ("order_number", "Order Number"),
    ("customer_name", "Customer"),
    ("inv_month", "Invoice Month"),
    ("completion_week", "Completion Week"),
    ("planned_week", "Planning Week"),
    ("sales_rep", "Sales Rep"),
    ("po_number", "PO Number"),
    ("value", "Value"),
    ("branch", "Branch"),
    ("customer_email", "Customer Email"),
]


def _confidence_label(conf):
    if conf >= 0.95:
        return "High"
    if conf >= 0.86:
        return "Medium"
    if conf > 0:
        return "Low"
    return "None"


def _mapping_info():
    diag = STATE.get("import_diag") or {}
    used = [r for r in diag.get("sheet_reports", []) if r.get("used")]
    if not used:
        return {"available_columns": [], "fields": [], "needs_confirmation": False,
                "sheet": "", "overrides": STATE.get("column_overrides", {})}
    primary = used[0]
    available = primary.get("columns_detected", [])
    fmap = primary.get("field_mapping", {})
    overrides = STATE.get("column_overrides", {})
    fields = []
    for field, label in MAPPABLE_FIELDS:
        info = fmap.get(field)
        conf = info["confidence"] if info else 0.0
        fields.append({
            "field": field, "label": label,
            "column": info["column"] if info else "",
            "confidence": conf,
            "confidence_label": _confidence_label(conf),
            "overridden": bool(info and info["overridden"]),
        })
    cw = fmap.get("completion_week")
    cw_confident = bool(cw) and (cw["overridden"] or cw["confidence"] >= 0.95)
    return {
        "sheet": primary.get("sheet", ""),
        "available_columns": available,
        "fields": fields,
        "overrides": overrides,
        "completion_week_column": cw["column"] if cw else "",
        "completion_week_confidence": _confidence_label(cw["confidence"]) if cw else "None",
        "completion_week_overridden": bool(cw and cw["overridden"]),
        "needs_confirmation": not cw_confident,
        "confirmed": bool(STATE.get("mapping_confirmed")),
    }


# ── Email HTML builder ────────────────────────────────────────────────────────
def _build_email_html(customer_name, orders, company, month_name, year):
    TH = (
        "background:#f1f5f9;font-size:10px;font-weight:700;color:#475569;"
        "text-transform:uppercase;letter-spacing:.05em;text-align:left;"
        "border-bottom:2px solid #cbd5e1;border-right:1px solid #e2e8f0;"
        "padding:9px 12px;white-space:nowrap;"
    )
    TH_LAST = TH.replace("border-right:1px solid #e2e8f0;", "border-right:none;")
    TD = (
        "padding:8px 12px;border-bottom:1px solid #e2e8f0;"
        "border-right:1px solid #e2e8f0;font-size:12px;color:#1e293b;"
        "vertical-align:middle;white-space:nowrap;"
    )
    TD_MONO = TD + "font-family:'Courier New',monospace;font-size:11.5px;"
    TD_MUTED = TD + "color:#64748b;"
    TD_BOLD = TD + "font-weight:600;"
    TD_GREEN_MID = TD + "font-weight:700;color:#2f5c1f;"
    TR_EVEN = "background:#f8fafc;"
    rows_html = ""
    for i, o in enumerate(orders):
        row_bg = TR_EVEN if i % 2 == 0 else ""
        status = o.get("order_status", "pending")
        rd_disp = o.get("ready_date_long", "-") or "-"
        raw_date = o.get("ready_date", "") or ""
        _msg_obj = {"action": "editDate", "orderNumber": str(o.get("order_number", "")), "currentDate": raw_date}
        _onclick = _html.escape(f"window.parent.postMessage({json.dumps(_msg_obj)}, '*')")
        edit_btn = (
            f"<button onclick=\"{_onclick}\" "
            f"style='margin-left:7px;background:none;border:1px solid #94a3b8;"
            f"border-radius:3px;padding:1px 6px;cursor:pointer;font-size:10px;"
            f"color:#475569;vertical-align:middle;line-height:1.4' "
            f"title='Edit ready date'>✏</button>"
        )
        check = " ✓" if status == "ready" else ""
        rd_cell = f"<td style='{TD_GREEN_MID}'>{rd_disp}{check}{edit_btn}</td>"
        rows_html += (
            f"<tr style='{row_bg}'>"
            f"<td style='{TD_MONO}'>{o.get('order_number', '—') or '—'}</td>"
            f"<td style='{TD_MONO}'>{o.get('po_number', '—') or '—'}</td>"
            f"<td style='{TD_MUTED}'>{o.get('booked_date', '—') or '—'}</td>"
            + rd_cell +
            f"<td style='{TD_BOLD}border-right:none;'>{_fmt_value(o.get('value', ''))}</td>"
            "</tr>"
        )
    total_val = 0
    for o in orders:
        try:
            total_val += float(str(o.get("value", "")).replace(",", ""))
        except Exception:
            pass
    total_str = _fmt_value(str(total_val)) if total_val else "—"
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#1e293b;">
  <div style="background:#fff;border-radius:10px;border:1px solid #dde3ec;box-shadow:0 2px 10px rgba(0,0,0,.07);">
    <div style="background:#323C8A;padding:18px 22px;border-radius:10px 10px 0 0;">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">
        <h2 style="margin:0;color:#fff;font-size:16px;font-weight:700;">Order Readiness Update</h2>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <span style="font-size:9px;font-weight:700;padding:3px 9px;border-radius:20px;background:#4273B8;color:#E8E7F5;">GAUGES</span>
          <span style="font-size:9px;font-weight:700;padding:3px 9px;border-radius:20px;background:#477A2B;color:#e8f3e0;">{month_name.upper()} {year}</span>
        </div>
      </div>
      <p style="margin:6px 0 0;color:#D2D0E9;font-size:11px;">{company} &mdash; Automated notification</p>
    </div>
    <div style="padding:18px 22px;font-size:13px;color:#1e293b;line-height:1.75;">
     Dear <strong>{customer_name}</strong>,<br><br>
    Greetings from <strong>{company}</strong>!<br><br>
We are pleased to inform you that the materials for the orders listed below are progressing as scheduled and are expected to be ready on the respective dates shown for <strong>{month_name} {year}</strong>.<br><br>
As a valued customer, we would like to ensure a seamless process. We kindly request you to review the schedule and make the necessary arrangements in advance to avoid any delays.<br><br>
Thank you for your continued confidence in <strong>{company}</strong>. We greatly appreciate your business and remain committed to delivering quality products and dependable service.<br><br>
<div style="overflow-x:auto;border:1px solid #e2e8f0;border-radius:8px;">
  <table style="border-collapse:collapse;table-layout:auto;width:max-content;min-width:580px;">
    <thead><tr>
      <th style="{TH}">Order No.</th>
      <th style="{TH}">PO No.</th>
      <th style="{TH}">Booked Date</th>
      <th style="{TH}">Expected Ready Date</th>
      <th style="{TH_LAST}">Value (&#8377;)</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<br>For any queries, please reach out to your dedicated sales representative or reply to this email.<br><br>
Warm regards,<br>
<strong>Sales &amp; Dispatch Team</strong><br>
{company}
    </div>
    <div style="background:#f8fafc;padding:12px 22px;border-top:1px solid #e2e8f0;border-radius:0 0 10px 10px;font-size:10.5px;color:#94a3b8;">
      Automated notification &middot; {company} &middot; Please do not reply directly
    </div>
  </div>
</body></html>"""


# ════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════════════════════════════════════

@api_bp.route("/health")
def health():
    from app.services.mappings import load_mappings_legacy
    db_ok = True
    try:
        engine = _get_db_engine()
        load_mappings_legacy(engine)
    except Exception as exc:
        log.error("Health check DB error: %s", exc)
        db_ok = False
    smtp_configured = bool(
        current_app.config.get("SMTP_USERNAME") and current_app.config.get("SMTP_APP_PASSWORD")
    )
    return jsonify({
        "status": "ok" if db_ok else "db_error",
        "database": "ok" if db_ok else "error",
        "smtp_configured": smtp_configured,
        "timestamp": datetime.utcnow().isoformat(),
    })


@api_bp.route("/readiness")
def readiness():
    db_ok = True
    try:
        engine = _get_db_engine()
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    smtp_configured = bool(
        current_app.config.get("SMTP_USERNAME") and current_app.config.get("SMTP_APP_PASSWORD")
    )
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "smtp_configured": smtp_configured,
    })


@api_bp.route("/export_mappings")
def export_mappings():
    engine = _get_db_engine()
    data = load_mappings_legacy(engine)
    js = json.dumps(data, ensure_ascii=False)
    from flask import Response
    return Response(
        js, status=200, mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=fm_mappings.json"},
    )


@api_bp.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return _err("NO_FILE", "No file uploaded")
    f = request.files["file"]
    try:
        raw = f.read()
        errors = validate_upload_file(
            f.filename, raw, max_mb=current_app.config.get("MAX_UPLOAD_MB", 50)
        )
        if errors:
            return _err("INVALID_FILE", errors[0], details=errors)

        STATE.update({
            "all_orders": [], "filtered_orders": [], "grouped": {},
            "import_diag": None, "filter_diag": None,
            "last_filename": f.filename, "last_file_bytes": raw,
            "column_overrides": {}, "mapping_confirmed": False,
        })

        result = EI.run_import(raw, f.filename)
        STATE["all_orders"] = result["records"]
        STATE["import_diag"] = result

        md = result["month_detection"]
        req_month = request.form.get("month")
        req_year = request.form.get("year")

        if md.get("month") and md.get("year") and md.get("confident"):
            STATE["sel_month"] = md["month"]
            STATE["sel_year"] = md["year"]
            period_source = "auto_detected"
        elif req_month and req_year:
            errs = validate_month_year(req_month, req_year)
            if errs:
                return _err("INVALID_PERIOD", errs[0])
            STATE["sel_month"] = int(req_month)
            STATE["sel_year"] = int(req_year)
            period_source = "user_selected"
        else:
            STATE["sel_month"] = None
            STATE["sel_year"] = None
            period_source = "needs_user_input"

        _apply_filter()

        engine = _get_db_engine()
        existing_emails = get_all_customer_emails(engine)
        new_emails = {}
        for o in result["records"]:
            cname = o.get("customer_name", "")
            cemail = o.get("customer_email", "")
            if cname and cemail and cname not in existing_emails and cname not in new_emails:
                new_emails[cname] = cemail
        if new_emails:
            bulk_upsert_customer_emails(engine, new_emails)

        log.info("Uploaded %s: %d total, %d imported, %d filtered",
                 f.filename, result["total_rows"], result["imported"],
                 len(STATE["filtered_orders"]))

        return _ok({
            "total": result["total_rows"],
            "imported": result["imported"],
            "filtered": len(STATE["filtered_orders"]),
            "customers": len(STATE["grouped"]),
            "orders": STATE["filtered_orders"],
            "sheets_used": result["sheets_used"],
            "sheets_skipped": result["sheets_skipped"],
            "month": STATE["sel_month"],
            "year": STATE["sel_year"],
            "period_source": period_source,
            "month_detection": result["month_detection"],
            "needs_month_selection": period_source == "needs_user_input",
            "diagnostics_summary": _diagnostics_summary(),
            "mapping": _mapping_info(),
        })
    except Exception as e:
        log.exception("Upload error")
        return _err("UPLOAD_ERROR", "Upload failed. See server logs.", http_status=500)


@api_bp.route("/remap", methods=["POST"])
def remap():
    if not STATE.get("last_file_bytes"):
        return _err("NO_FILE", "No datasheet loaded. Please upload a file first.")
    data = request.get_json(silent=True) or {}
    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        return _err("INVALID_INPUT", "overrides must be an object")
    clean = {k: ("" if v is None else str(v))
             for k, v in overrides.items() if k in EI.CANONICAL_FIELDS}
    try:
        result = EI.run_import(STATE["last_file_bytes"],
                               STATE.get("last_filename", ""),
                               overrides=clean)
        STATE["column_overrides"] = clean
        STATE["mapping_confirmed"] = True
        STATE["all_orders"] = result["records"]
        STATE["import_diag"] = result
        if not STATE["sel_month"] or not STATE["sel_year"]:
            md = result["month_detection"]
            if md.get("month") and md.get("year") and md.get("confident"):
                STATE["sel_month"] = md["month"]
                STATE["sel_year"] = md["year"]
        _apply_filter()
        return _ok({
            "total": result["total_rows"],
            "imported": result["imported"],
            "filtered": len(STATE["filtered_orders"]),
            "customers": len(STATE["grouped"]),
            "orders": STATE["filtered_orders"],
            "month": STATE["sel_month"],
            "year": STATE["sel_year"],
            "sheets_used": result["sheets_used"],
            "diagnostics_summary": _diagnostics_summary(),
            "mapping": _mapping_info(),
        })
    except Exception as e:
        log.exception("Remap error")
        return _err("REMAP_ERROR", "Remap failed.", http_status=500)


@api_bp.route("/diagnostics")
def diagnostics():
    diag = STATE.get("import_diag") or {}
    weeks_detected = sorted({
        w for o in STATE.get("all_orders", [])
        if (w := EI.normalise_week(o.get("completion_week", ""))) is not None
    })
    return _ok({
        "summary": _diagnostics_summary(),
        "weeks_detected": weeks_detected,
        "sel_month": STATE["sel_month"],
        "sel_year": STATE["sel_year"],
    })


@api_bp.route("/set_month", methods=["POST"])
def set_month():
    data = request.get_json(silent=True) or {}
    month = data.get("month")
    year = data.get("year")
    errs = validate_month_year(month, year)
    if errs:
        return _err("INVALID_PERIOD", errs[0])
    STATE["sel_month"] = int(month)
    STATE["sel_year"] = int(year)
    if STATE["all_orders"]:
        _apply_filter()
    return _ok({
        "filtered": len(STATE["filtered_orders"]),
        "customers": len(STATE["grouped"]),
        "orders": STATE["filtered_orders"],
        "diagnostics_summary": _diagnostics_summary(),
    })


@api_bp.route("/customers")
def customers():
    custs = []
    for k, v in STATE["grouped"].items():
        custs.append({
            "customer_name": k,
            "order_count": len(v["orders"]),
            "has_date": any(o.get("ready_date") or o.get("order_status") == "ready"
                            for o in v["orders"]),
        })
    return _ok(custs)


@api_bp.route("/preview/<path:customer_name>")
def preview(customer_name):
    grp = STATE["grouped"].get(customer_name)
    if not grp:
        return _err("NOT_FOUND", "Customer not found", http_status=404)
    if not STATE.get("sel_month"):
        return _err("NO_PERIOD", "No invoice month selected")

    engine = _get_db_engine()
    mappings = load_mappings_legacy(engine)
    company = mappings.get("config", {}).get("company", current_app.config.get("COMPANY_NAME", "FORBES MARSHALL HYD PVT LTD"))
    month_name = MONTHS_LONG[STATE["sel_month"] - 1]

    resolution = resolve_recipients(
        customer_name,
        grp["orders"],
        mappings,
        override_customer_emails=None,
    )

    from_display = (mappings.get("config", {}).get("gmail", "") or
                    current_app.config.get("SMTP_USERNAME", "") or
                    "(invoice sender)")
    to_display = ", ".join(resolution["to"]) if resolution["to"] else "(no customer email)"
    cc_display = ", ".join(resolution["cc"]) if resolution["cc"] else "(no CC emails)"

    po = next((o["po_number"] for o in grp["orders"] if o.get("po_number")), "")
    subj = f"Order Readiness Update – {company} | {customer_name}" + (f" | PO: {po}" if po else "")
    html = _build_email_html(customer_name, grp["orders"], company, month_name, STATE["sel_year"])

    return _ok({
        "html": html,
        "subject": subj,
        "from": from_display,
        "to": to_display,
        "cc": cc_display,
        "warnings": resolution["warnings"],
        "errors": resolution["errors"],
        "customer_emails": mappings.get("customer_emails", {}).get(customer_name, ""),
    })


@api_bp.route("/mappings", methods=["GET"])
def get_mappings():
    engine = _get_db_engine()
    return jsonify(load_mappings_legacy(engine))


@api_bp.route("/mappings", methods=["POST"])
def save_mappings():
    data = request.get_json(silent=True) or {}
    engine = _get_db_engine()
    existing = load_mappings_legacy(engine)
    for key in ["sales", "branch", "invoice", "config", "customer_emails", "contacts"]:
        if key in data:
            existing[key] = data[key]
    save_mappings_legacy(engine, existing)
    if "customer_emails" in data and isinstance(data["customer_emails"], dict):
        for cname, emails in data["customer_emails"].items():
            upsert_customer_email(engine, cname, emails)
    log.info("Mappings saved via API")
    return _ok()


@api_bp.route("/meta")
def meta():
    reps = sorted({o["sales_rep"] for o in STATE["filtered_orders"]
                   if o.get("sales_rep") and o["sales_rep"] != "No Sales Credit"})
    branches = sorted({o["branch"] for o in STATE["filtered_orders"] if o.get("branch")})
    cust_emails = {}
    for o in STATE["filtered_orders"]:
        cname = o.get("customer_name", "")
        cemail = o.get("customer_email", "")
        if cname and cemail:
            existing = cust_emails.get(cname, set())
            for e in cemail.split(","):
                e = e.strip()
                if e:
                    existing.add(e)
            cust_emails[cname] = existing
    cust_emails_str = {k: ", ".join(sorted(v)) for k, v in cust_emails.items()}
    return _ok({"reps": reps, "branches": branches, "customer_emails": cust_emails_str})


@api_bp.route("/orders")
def orders():
    return _ok(STATE["filtered_orders"])


@api_bp.route("/orders/filter", methods=["POST"])
def orders_filter():
    criteria = request.get_json(silent=True) or {}
    result = apply_filter_criteria(STATE["filtered_orders"], criteria)
    return _ok(result)


@api_bp.route("/status")
def status():
    return _ok({
        "total": len(STATE["all_orders"]),
        "filtered": len(STATE["filtered_orders"]),
        "customers": len(STATE["grouped"]),
        "month": STATE["sel_month"],
        "year": STATE["sel_year"],
    })


@api_bp.route("/upload_contacts", methods=["POST"])
def upload_contacts():
    if "file" not in request.files:
        return _err("NO_FILE", "No file uploaded")
    f = request.files["file"]
    try:
        raw = f.read()
        contacts = extract_contacts(raw, f.filename)
        if not contacts:
            return _err("NO_CONTACTS", "No valid contacts found in the file.")

        engine = _get_db_engine()
        mappings = load_mappings_legacy(engine)
        sales_map = mappings.get("sales", {})
        branch_map = mappings.get("branch", {})

        # Seed maps from the currently loaded order sheet so that contacts
        # can be matched even when nothing has been saved to the DB yet.
        for o in STATE["filtered_orders"]:
            rep = o.get("sales_rep", "")
            if rep and rep != "No Sales Credit" and rep not in sales_map:
                sales_map[rep] = ""
            br = o.get("branch", "")
            if br and br not in branch_map:
                branch_map[br] = ""

        # Build contacts lookup: name_lower → [email, …] (collect all for same name)
        contacts_multi: dict[str, list[str]] = {}
        for c in contacts:
            key = c["name"].lower().strip()
            email = (c.get("email") or "").strip()
            if not key:
                continue
            if key not in contacts_multi:
                contacts_multi[key] = []
            if email and email not in contacts_multi[key]:
                contacts_multi[key].append(email)

        def find_match(target_name) -> str | None:
            """Return comma-joined emails for the best-matching contact."""
            tl = target_name.lower().strip()
            # Exact match
            if tl in contacts_multi:
                return ", ".join(contacts_multi[tl])
            # Substring / word overlap
            target_parts = {p for p in tl.replace(",", " ").split() if len(p) > 2}
            best_emails: list[str] = []
            for cname, cemails in contacts_multi.items():
                if cname in tl or tl in cname:
                    best_emails = cemails
                    break
                contact_parts = {p for p in cname.replace(",", " ").split() if len(p) > 2}
                if target_parts and contact_parts and target_parts & contact_parts:
                    best_emails = cemails
                    break
            return ", ".join(best_emails) if best_emails else None

        force = request.form.get("force", "false").lower() == "true"

        # ── Update existing sales reps (fill empty entries or forced re-match) ──
        sales_updated = 0
        for rep in list(sales_map.keys()):
            if force or not sales_map.get(rep):
                m = find_match(rep)
                if m:
                    sales_map[rep] = m
                    sales_updated += 1

        # ── Auto-add NEW sales rep names discovered via each contact's
        #    sales_rep field (e.g. Google Contacts has "Organization Title"
        #    or a custom "Sales Rep" column).  Only add if not already
        #    present — never overwrite existing entries. ────────────────────
        new_reps_added = 0
        for c in contacts:
            rep_name = (c.get("sales_rep") or "").strip()
            if not rep_name:
                continue
            if rep_name not in sales_map:
                # New rep — try to find their own email from contacts list
                email_for_rep = find_match(rep_name) or ""
                sales_map[rep_name] = email_for_rep
                new_reps_added += 1
            elif not sales_map[rep_name]:
                # Rep exists but has no email yet — fill it in
                m = find_match(rep_name)
                if m:
                    sales_map[rep_name] = m
                    sales_updated += 1

        # ── Update existing branch entries ────────────────────────────────
        branch_updated = 0
        for br in list(branch_map.keys()):
            if force or not branch_map.get(br):
                m = find_match(br)
                if m:
                    branch_map[br] = m
                    branch_updated += 1

        # ── Merge contacts dict (new entries added, existing NOT overwritten) ─
        existing_contacts = mappings.get("contacts", {})
        for c in contacts:
            cname = c["name"]
            cemail = (c.get("email") or "").strip()
            if cname not in existing_contacts or not existing_contacts[cname]:
                existing_contacts[cname] = cemail  # add or fill blank

        mappings["sales"] = sales_map
        mappings["branch"] = branch_map
        mappings["contacts"] = existing_contacts
        # save_mappings_legacy writes to DB AND mappings.json (dual-write)
        save_mappings_legacy(engine, mappings)

        return _ok({
            "total_contacts": len(contacts),
            "sales_updated": sales_updated,
            "branch_updated": branch_updated,
            "new_reps_added": new_reps_added,
            "contacts_preview": contacts[:20],
            "sales": sales_map,
            "branch": branch_map,
        })
    except Exception:
        log.exception("Contacts upload error")
        return _err("CONTACTS_ERROR", "Contact import failed.", http_status=500)


@api_bp.route("/send/start", methods=["POST"])
def send_start():
    if not STATE["filtered_orders"]:
        return _err("NO_ORDERS", "No filtered orders. Upload a file and set month first.")

    engine = _get_db_engine()
    mappings = load_mappings_legacy(engine)
    cfg = mappings.get("config", {})
    company = cfg.get("company", current_app.config.get("COMPANY_NAME", "FORBES MARSHALL HYD PVT LTD"))
    # Environment variable SEND_MODE takes precedence over UI/DB config.
    # This ensures Render's SEND_MODE=brevo is always respected.
    mode = current_app.config.get("SEND_MODE") or cfg.get("send_mode", "smtp")

    if mode == "n8n":
        if not cfg.get("n8n_webhook", "").strip():
            return _err("CONFIG_ERROR", "n8n webhook URL not configured.")
    elif mode == "brevo":
        brevo_key = current_app.config.get("BREVO_API_KEY", "").strip()
        if not brevo_key:
            return _err("CONFIG_ERROR", "Brevo API key not configured. Set BREVO_API_KEY in environment.")
    else:
        # SMTP mode
        smtp_user = cfg.get("gmail", "") or current_app.config.get("SMTP_USERNAME", "")
        smtp_pass = cfg.get("password", "") or current_app.config.get("SMTP_APP_PASSWORD", "")
        if not smtp_user or not smtp_pass:
            return _err("CONFIG_ERROR", "SMTP username and password not configured.")

    opts = request.get_json(silent=True) or {}
    week_only = opts.get("week_only", False)
    skip_sent = opts.get("skip_sent", True)
    already_sent = set(opts.get("sent_keys", []))
    filter_names = opts.get("filter_names", None)
    email_overrides = opts.get("email_overrides", {})

    month = STATE["sel_month"]
    year = STATE["sel_year"]
    if not month or not year:
        return _err("NO_PERIOD", "No invoice month/year selected.")

    month_name = MONTHS_LONG[month - 1]

    custs = list(STATE["grouped"].values())
    if filter_names:
        custs = [c for c in custs if c["customer_name"] in filter_names]
    elif week_only:
        custs = [c for c in custs if any(o.get("working_week") for o in c["orders"])]
    if skip_sent and not filter_names:
        custs = [c for c in custs if c["customer_name"] not in already_sent]
    if not custs:
        return _err("NO_CUSTOMERS", "No eligible customers after filters.")

    batch_id = str(uuid.uuid4())
    jobs_data = []
    skipped_no_email = []

    cust_email_map = mappings.get("customer_emails", {})

    for grp in custs:
        cname = grp["customer_name"]
        orders = grp["orders"]
        override = email_overrides.get(cname) or cust_email_map.get(cname, "")
        resolution = resolve_recipients(cname, orders, mappings, override or None)

        if not resolution["to"]:
            skipped_no_email.append(cname)
            continue

        order_numbers = [o.get("order_number", "") for o in orders]
        ikey = make_idempotency_key(cname, month, year, order_numbers, resolution["to"])

        po = next((o["po_number"] for o in orders if o.get("po_number")), "")
        subject = (f"Order Readiness Update – {company} | {cname}" +
                   (f" | PO: {po}" if po else ""))
        body_html = _build_email_html(cname, orders, company, month_name, year)

        jobs_data.append({
            "idempotency_key": ikey,
            "customer": cname,
            "to": resolution["to"],
            "cc": resolution["cc"],
            "subject": subject,
            "body_html": body_html,
            "invoice_month": month,
            "invoice_year": year,
        })

    created_ids = create_email_jobs(db.session, batch_id, jobs_data)

    # Build dispatch config
    dispatch_cfg = {
        "send_mode": mode,
        "n8n_webhook": cfg.get("n8n_webhook", ""),
        "smtp_host": cfg.get("smtp_host", current_app.config.get("SMTP_HOST", "smtp.gmail.com")),
        "smtp_port": cfg.get("smtp_port", current_app.config.get("SMTP_PORT", 587)),
        "smtp_use_tls": current_app.config.get("SMTP_USE_TLS", True),
        "smtp_username": cfg.get("gmail", "") or current_app.config.get("SMTP_USERNAME", ""),
        "smtp_password": cfg.get("password", "") or current_app.config.get("SMTP_APP_PASSWORD", ""),
        "gmail": cfg.get("gmail", ""),
        "password": cfg.get("password", ""),
        # Brevo (HTTPS email API — no SMTP port needed, works on Render free tier)
        "brevo_api_key": current_app.config.get("BREVO_API_KEY", ""),
        "brevo_sender_email": (
            current_app.config.get("BREVO_SENDER_EMAIL", "")
            or cfg.get("gmail", "")
            or current_app.config.get("SMTP_USERNAME", "")
        ),
        "company": company,
        "email_timeout": current_app.config.get("EMAIL_TIMEOUT", 30),
        "max_retries": current_app.config.get("MAX_EMAIL_RETRIES", 3),
    }

    dispatch_batch(current_app._get_current_object(), created_ids, dispatch_cfg)

    return _ok({
        "batch_id": batch_id,
        "total": len(custs),
        "jobs_created": len(created_ids),
        "skipped_no_email": skipped_no_email,
    })


@api_bp.route("/send/status/<batch_id>")
def send_status(batch_id):
    status_data = get_batch_status(db.session, batch_id)
    if not status_data.get("found"):
        return _err("NOT_FOUND", f"Batch {batch_id!r} not found.", http_status=404)
    return _ok(status_data)


@api_bp.route("/orders/<path:order_number>/date", methods=["POST"])
def update_order_date(order_number):
    """Update the ready_date for a specific order (in-session override)."""
    data = request.get_json(silent=True) or {}
    new_date = (data.get("ready_date") or "").strip()

    # Validate: must be YYYY-MM-DD or empty string (clear the date)
    import re as _re
    if new_date and not _re.match(r"^\d{4}-\d{2}-\d{2}$", new_date):
        return _err("INVALID_DATE", "Date must be in YYYY-MM-DD format.", 400)

    updated = False
    for o in STATE["all_orders"]:
        if str(o.get("order_number", "")).strip() == str(order_number).strip():
            if new_date:
                o["ready_date"] = new_date
                # Compute the human-readable long form (e.g. "14 September 2026")
                # so _apply_filter() can use it without re-parsing.
                try:
                    y, m, day = new_date.split("-")
                    o["ready_date_long"] = _fmt_date_long(date(int(y), int(m), int(day)))
                except Exception:
                    o["ready_date_long"] = new_date
                # Always flag as overridden regardless of current order_status
                o["_date_overridden"] = True
            else:
                o["ready_date"] = None
                o["ready_date_long"] = None
                o.pop("_date_overridden", None)
            updated = True
            break

    if not updated:
        return _err("NOT_FOUND", f"Order {order_number} not found in current session.", 404)

    # Recompute filtered orders so preview picks up the new date
    _apply_filter()

    return _ok({"order_number": order_number, "ready_date": new_date})


# Legacy endpoint kept for backward compatibility
@api_bp.route("/send", methods=["POST"])
def send_legacy():
    """Redirect to new send/start endpoint."""
    return send_start()


@api_bp.route("/send_log")
def send_log():
    """Legacy send log endpoint — returns recent send logs from DB."""
    from app.models.send_log import SendLog
    logs = db.session.query(SendLog).order_by(SendLog.sent_at.desc()).limit(100).all()
    return _ok([sl.to_dict() for sl in logs])
