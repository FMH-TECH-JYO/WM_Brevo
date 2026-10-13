"""
excel_ingest.py — Schema-flexible Excel/CSV ingestion pipeline
================================================================
Turns an uploaded workbook (arbitrary headers, arbitrary sheet layout,
arbitrary month/week wording) into a list of canonical order records plus
a full diagnostics report explaining exactly what happened to every row.

Pipeline (see README-style docstring at bottom of file for the full picture):

    Excel file
       -> workbook/sheet detection
       -> header-row detection (per sheet)
       -> header normalisation
       -> alias / fuzzy matching -> canonical schema
       -> value normalisation (dates, months, weeks, numbers)
       -> business-rule validation (required fields, duplicates)
       -> combined records + diagnostics report

Nothing in this module is specific to any one month's file name, sheet
name, or header wording — everything is driven by the ALIASES table and
the normalisation helpers below, so new files with different headers do
not require code changes, only (optionally) new alias entries.
"""

from __future__ import annotations

import io
import os
import re
import json
import logging
import difflib
import datetime as _dt
from collections import Counter, defaultdict

import pandas as pd

log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
#  CANONICAL SCHEMA
# ══════════════════════════════════════════════════════════════════════════

# Fields without which a row cannot be treated as a usable order.
REQUIRED_FIELDS = ["customer_name", "order_number"]

# Every canonical field the importer understands. Anything not listed here
# that still gets a column in the sheet is preserved as an "extra" column
# (see _EXTRA_PREFIX) rather than being dropped.
CANONICAL_FIELDS = [
    "order_number", "customer_name", "customer_email", "branch", "division",
    "po_number", "value", "inv_month", "planned_week", "completion_week",
    "hold_details", "sales_rep", "item_code", "shipping", "payment_terms",
    "request_date", "promise_date", "schedule_ship_date", "oa_status",
    "booked_date", "excel_ready_date", "freight_terms", "inspection_agency",
    "bill_to_contact", "spcl_cases",
]

_EXTRA_PREFIX = "extra__"

# ══════════════════════════════════════════════════════════════════════════
#  HEADER ALIASES
#  Keys are already "cleaned" (see _clean_header) phrases. Add more here as
#  new header wordings are encountered — no other code changes needed.
# ══════════════════════════════════════════════════════════════════════════

ALIASES: dict[str, str] = {
    # order number
    "order number": "order_number", "order no": "order_number", "order num": "order_number",
    "order id": "order_number", "order #": "order_number", "sales order": "order_number",
    "so number": "order_number", "so no": "order_number", "oa number": "order_number",
    "oa no": "order_number",
    # customer
    "customer name": "customer_name", "customer": "customer_name", "client": "customer_name",
    "client name": "customer_name", "cust name": "customer_name", "account name": "customer_name",
    # customer email
    "customer email": "customer_email", "customer email id": "customer_email",
    "customer emails": "customer_email", "customer mail": "customer_email",
    "customer mail id": "customer_email", "client email": "customer_email",
    "email id": "customer_email", "bill to email address": "customer_email",
    "bill to email": "customer_email",
    # branch / division
    "branch": "branch", "location": "branch", "branch name": "branch",
    "division": "division",
    # po
    "po": "po_number", "po number": "po_number", "po no": "po_number", "po no.": "po_number",
    "purchase order": "po_number", "cust po number": "po_number", "customer po": "po_number",
    "po #": "po_number",
    # value
    "value": "value", "order value": "value", "amount": "value", "invoice value": "value",
    # invoice month
    "invoice month": "inv_month", "inv month": "inv_month", "invoice mnth": "inv_month",
    "inv mnth": "inv_month", "month": "inv_month", "may inv month": "inv_month",
    "inv.month": "inv_month",
    # planned week
    "planned week": "planned_week", "planning week": "planned_week", "plan week": "planned_week",
    "week planned": "planned_week",
    # completion week — "Comp.week" / "Comp Week" / "Compl Week" are the
    # abbreviated forms actually used on real monthly datasheets, not just
    # the spelled-out "Completion Week".
    "completion week": "completion_week", "completed week": "completion_week",
    "completion": "completion_week", "comp week": "completion_week",
    "comp wk": "completion_week", "compl week": "completion_week",
    "compln week": "completion_week", "comp": "completion_week",
    # status / hold
    "status": "oa_status", "oa status": "oa_status",
    "hold details": "hold_details", "hold reason": "hold_details", "hold": "hold_details",
    # sales rep
    "sales rep": "sales_rep", "salesperson": "sales_rep", "sales person": "sales_rep",
    "sales representative": "sales_rep", "account manager": "sales_rep",
    # dates
    "request date": "request_date", "requested date": "request_date",
    "promise date": "promise_date",
    "schedule ship date": "schedule_ship_date", "scheduled ship date": "schedule_ship_date",
    "ship date": "schedule_ship_date",
    "booked date": "booked_date", "booking date": "booked_date", "order date": "booked_date",
    "ready date": "excel_ready_date", "reservation ready since": "excel_ready_date",
    # item
    "item code": "item_code", "itemcode": "item_code", "sku": "item_code",
    "product code": "item_code",
    # misc
    "shipping instructions": "shipping",
    "bill to contact": "bill_to_contact", "bill to contact name": "bill_to_contact",
    "freight terms": "freight_terms",
    "inspection agency": "inspection_agency",
    "terms of payment": "payment_terms", "payment terms": "payment_terms",
    "spcl cases": "spcl_cases", "special cases": "spcl_cases",
}

# Keyword-group fallback rules, used only when an exact alias lookup misses.
# Each rule is (required_token_groups, canonical_field); every group must
# have at least one of its tokens present in the cleaned header's token set.
_KEYWORD_RULES: list[tuple[list[set[str]], str]] = [
    ([{"order"}, {"number", "no", "num", "id"}],                      "order_number"),
    ([{"sales"}, {"order"}],                                          "order_number"),
    ([{"customer", "client", "account"}, {"name"}],                   "customer_name"),
    ([{"customer", "client", "bill"}, {"email", "mail"}],             "customer_email"),
    ([{"po", "purchase"}, {"no", "number", "order"}],                 "po_number"),
    ([{"planned", "planning", "plan"}, {"week"}],                     "planned_week"),
    ([{"completion", "completed", "comp", "compl", "compln"}, {"week", "wk"}],
                                                                        "completion_week"),
    ([{"invoice", "inv"}, {"month", "mnth"}],                         "inv_month"),
    ([{"sales"}, {"rep", "person", "representative"}],                "sales_rep"),
    ([{"account"}, {"manager"}],                                      "sales_rep"),
    ([{"hold"}, {"detail", "details", "reason"}],                     "hold_details"),
    ([{"request", "requested"}, {"date"}],                            "request_date"),
    ([{"promise"}, {"date"}],                                         "promise_date"),
    ([{"schedule", "scheduled"}, {"ship", "shipping"}],               "schedule_ship_date"),
    ([{"booked", "booking", "order"}, {"date"}],                      "booked_date"),
    ([{"item", "product"}, {"code"}],                                 "item_code"),
    ([{"bill"}, {"contact"}],                                         "bill_to_contact"),
    ([{"freight"}, {"terms"}],                                        "freight_terms"),
    ([{"inspection"}, {"agency"}],                                    "inspection_agency"),
    ([{"terms", "payment"}, {"payment", "terms"}],                    "payment_terms"),
    ([{"shipping"}, {"instruction", "instructions"}],                 "shipping"),
]

# Flat (alias_phrase, canonical) list for fuzzy matching fallback.
_FUZZY_CANDIDATES = list(ALIASES.items())
_FUZZY_THRESHOLD = 0.84


def _clean_header(name) -> str:
    """Lowercase, strip punctuation/brackets, collapse separators & spaces,
    light singularisation — so 'Planning-Week', 'PLANNING_WEEK',
    'Planning Weeks', 'Comp.week' etc. all become 'planning week' / 'comp
    week'. "." is treated as a word separator (like "_" and "-") rather
    than preserved, so an abbreviation like "Comp.week" tokenises the same
    way as "Comp Week" for the alias table and keyword-rule matching below
    — every alias that used to rely on a literal "." (e.g. "po no.",
    "inv.month") already has a period-free duplicate ("po no", "inv
    month"), so nothing that matched before stops matching."""
    raw = str(name).strip().lower()
    raw = raw.replace("_", " ").replace("-", " ").replace(".", " ")
    raw = re.sub(r"[^\w\s/#]", " ", raw)   # drop brackets/punctuation, keep / # (used in some aliases)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _singularise(cleaned: str) -> str:
    """Light plural -> singular so 'Planned Weeks' matches 'planned week'.
    Applied as a *fallback* only, never in place of the literal form, so
    words that merely end in 's' (sales, status, terms) still match their
    exact aliases first."""
    out = []
    for w in cleaned.split(" "):
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("es") and not w.endswith(("ses", "xes")):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return " ".join(out)


def normalise_col(raw_name) -> tuple[str, float]:
    """Map one raw header to a canonical field name.
    Returns (canonical_or_extra_key, confidence 0..1)."""
    cleaned = _clean_header(raw_name)
    if not cleaned:
        return (_EXTRA_PREFIX + "blank", 0.0)

    if cleaned in ALIASES:
        return (ALIASES[cleaned], 1.0)

    singular = _singularise(cleaned)
    if singular != cleaned and singular in ALIASES:
        return (ALIASES[singular], 1.0)

    tokens = set(cleaned.split()) | set(singular.split())
    for groups, canonical in _KEYWORD_RULES:
        if all(tokens & g for g in groups):
            return (canonical, 0.9)

    # fuzzy fallback
    best_field, best_score = None, 0.0
    for phrase, canonical in _FUZZY_CANDIDATES:
        score = difflib.SequenceMatcher(None, cleaned, phrase).ratio()
        if score > best_score:
            best_field, best_score = canonical, score
    if best_score >= _FUZZY_THRESHOLD:
        return (best_field, best_score)

    # unmapped — preserved as an extra/raw column, not silently dropped
    return (_EXTRA_PREFIX + cleaned.replace(" ", "_"), 0.0)


# ══════════════════════════════════════════════════════════════════════════
#  HEADER-ROW DETECTION
# ══════════════════════════════════════════════════════════════════════════

def _score_row_as_header(cells) -> int:
    """How many cells in this row look like recognised business-field
    headers (exact alias or keyword-rule match)?"""
    score = 0
    for c in cells:
        if c is None or (isinstance(c, float) and pd.isna(c)):
            continue
        s = str(c).strip()
        if not s:
            continue
        field, conf = normalise_col(s)
        if not field.startswith(_EXTRA_PREFIX) and conf >= 0.85:
            score += 1
    return score


def _detect_header_row(raw_df: pd.DataFrame, scan_rows: int = 15) -> int:
    """raw_df must have been read with header=None. Returns the best guess
    row index to use as the header row."""
    best_idx, best_score = 0, -1
    limit = min(scan_rows, len(raw_df))
    for i in range(limit):
        row_vals = raw_df.iloc[i].tolist()
        score = _score_row_as_header(row_vals)
        if score > best_score:
            best_idx, best_score = i, score
    # Require at least 2 recognisable headers to trust auto-detection;
    # otherwise assume the sheet's first row is the header (safe default).
    return best_idx if best_score >= 2 else 0


# ══════════════════════════════════════════════════════════════════════════
#  VALUE NORMALISATION
# ══════════════════════════════════════════════════════════════════════════

MONTHS_LONG  = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"]
MONTHS_SHORT = ["JAN","FEB","MAR","APR","MAY","JUN",
                "JUL","AUG","SEP","OCT","NOV","DEC"]

_MONTH_NAME_TO_NUM = {}
for _i, _a in enumerate(MONTHS_SHORT):
    _MONTH_NAME_TO_NUM[_a] = _i + 1
for _i, _n in enumerate(MONTHS_LONG):
    _MONTH_NAME_TO_NUM[_n.upper()] = _i + 1

# Status-like tokens that must NOT be misread as a month even though they
# might sit in an "Inv Month" / "Completion Week" column.
_STATUS_TOKENS = {"HOLD", "PP", "R", "NTH", "DI", "LC", "LD", "INS", "PENDING", "READY"}


def _format_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    if isinstance(v, pd.Timestamp):
        return "" if pd.isna(v) else v.strftime("%Y-%m-%d")
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if s.lower() in ("nat", "none", "nan"):
        return ""
    return s


def extract_month_year(text) -> tuple[int | None, int | None]:
    """Pull a (month, year) pair out of free text such as:
    'SEP', 'September', 'Sep-26', '2026-01', 'January 2026', '01', '1'.
    Returns (None, None) if nothing month-like is found, or if the text
    looks like a status code rather than a month (e.g. 'HOLD', 'PP', 'R')."""
    if text is None:
        return (None, None)
    raw = str(text).strip()
    if not raw:
        return (None, None)
    up = raw.upper().strip()

    if up in _STATUS_TOKENS:
        return (None, None)

    # ISO-ish "2026-01" / "2026/01"
    m = re.match(r"^(\d{4})[-/](\d{1,2})$", up)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return (mo, y)

    # "Jan-26" / "SEP-2026" / "Jan 26"
    m = re.match(r"^([A-Za-z]{3,9})[\s\-]?(\d{2,4})?$", raw)
    if m:
        name = m.group(1).upper()
        if name in _MONTH_NAME_TO_NUM:
            mo = _MONTH_NAME_TO_NUM[name]
            yr = None
            if m.group(2):
                yy = int(m.group(2))
                yr = 2000 + yy if yy < 100 else yy
            return (mo, yr)

    # "January 2026" / "September 2026"
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{4})$", raw)
    if m and m.group(1).upper() in _MONTH_NAME_TO_NUM:
        return (_MONTH_NAME_TO_NUM[m.group(1).upper()], int(m.group(2)))

    # Plain numeric month "01".."12" / "1".."12" — only trust this in a
    # dedicated month-ish context, handled by the caller; here we still
    # return it since the caller already knows the column is inv_month.
    if re.match(r"^\d{1,2}$", up):
        n = int(up)
        if 1 <= n <= 12:
            return (n, None)

    return (None, None)


def extract_month_abbr_from_text(text) -> str | None:
    """Find a month token embedded anywhere in free text, e.g.
    'Ready by 1st Sep' -> 'SEP'. Unlike extract_month_year this scans for a
    substring match rather than requiring the whole cell to be a month."""
    if not text:
        return None
    t = str(text).upper()
    for name, idx in _MONTH_NAME_TO_NUM.items():
        if name in t:
            return MONTHS_SHORT[idx - 1]
    return None


def extract_day(text) -> int | None:
    m = re.search(r"(\d{1,2})", str(text) or "")
    if m:
        d = int(m.group(1))
        if 1 <= d <= 31:
            return d
    return None


_WEEK_RE = re.compile(r"^(?:WEEK|WK|W)?[\s\-_]*([0-9]{1,2})$")


def normalise_week(raw) -> int | None:
    """Understands WEEK1, WEEK 1, Week-1, W1, WK1, WK 1, 1, 01, Week01,
    Week 01, WEEK12, etc. Returns the week number (no artificial WEEK1-4
    ceiling — WEEK5/6/7... are recognised too) or None if the value isn't
    week-shaped at all (e.g. 'R', 'PP', 'HOLD', 'SEP')."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s:
        return None
    s_compact = re.sub(r"[\s\-_]", "", s)
    m = _WEEK_RE.match(s_compact) or _WEEK_RE.match(s)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    if 1 <= n <= 53:
        return n
    return None


def is_hold_value(raw) -> bool:
    return "HOLD" in str(raw or "").upper()


def is_ready_flag(raw) -> bool:
    return str(raw or "").strip().upper() == "R"


def is_ready_text(raw) -> bool:
    return "READY" in str(raw or "").upper()


# ══════════════════════════════════════════════════════════════════════════
#  SHEET-LEVEL PARSING
# ══════════════════════════════════════════════════════════════════════════

PERIOD_SIGNAL_FIELDS = {"inv_month", "completion_week", "planned_week"}


def _is_order_sheet(mapped_fields: set) -> bool:
    has_customer = "customer_name" in mapped_fields
    has_id       = "order_number" in mapped_fields or "value" in mapped_fields
    has_period   = bool(mapped_fields & PERIOD_SIGNAL_FIELDS)
    return has_customer and has_id and has_period


def _read_all_sheets(file_bytes, filename) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".csv":
        return {"CSV": pd.read_csv(io.BytesIO(file_bytes), dtype=str, header=None)}
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, header=None)


# Same month-name family used by the month/year detector below, reused here
# so the "<Month> INV" sheet-naming convention is recognised for any month
# without ever hard-coding one month's name.
_MONTH_TOKEN_FOR_SHEET_RE = (
    r"(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|"
    r"AUG(?:UST)?|SEP(?:T)?(?:EMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
)
_MONTH_INV_SHEET_RE = re.compile(
    r"\b" + _MONTH_TOKEN_FOR_SHEET_RE + r"\b[\s_\-]*INV\w*\b", re.IGNORECASE
)


def _select_primary_sheets(sheet_names: list) -> list:
    """
    The monthly datasheet follows a "<Month> INV" naming convention for the
    one tab that actually holds that period's orders (e.g. 'SEP INV',
    'OCT INV', 'November INV'). Every other tab in the same workbook — rate
    breakdowns, per-branch pivots, or a customer-specific reference list
    such as 'Thermax OAs' — can *structurally* look like an order sheet
    (Customer Name + Order Number + a period column) without actually being
    this period's invoicing sheet, and must not be mixed into the mailer.

    When one or more sheets match the "<Month> INV" naming pattern, ONLY
    those sheets are treated as order data — every other tab is skipped
    regardless of whether it would otherwise pass the structural check.
    This is driven purely by the wording ("<any month name> INV"), never by
    a specific month or file name, so the same rule applies unchanged to
    every future month's upload — SEP INV this month, OCT INV next month,
    and so on.

    When no sheet in the workbook matches this naming pattern at all (a
    file that doesn't use this convention), nothing is restricted here —
    sheet selection falls back entirely to the structural Customer + Order
    + period check in `_is_order_sheet`, exactly as before.
    """
    return [sn for sn in sheet_names if _MONTH_INV_SHEET_RE.search(str(sn))]


def _parse_sheet(sheet_name: str, raw_df: pd.DataFrame, report: dict,
                 overrides: dict | None = None):
    """Detect header row, build the column mapping, and yield canonical
    records + a skipped-row log for this one sheet. Mutates `report`.

    `overrides` is an optional {canonical_field: exact_excel_header} map
    supplied by the user from the mapping UI. It is applied on top of the
    automatic mapping, so automatic detection stays the default and a
    manual choice is purely a correction.
    """
    header_idx = _detect_header_row(raw_df)
    header_cells = raw_df.iloc[header_idx].tolist()

    col_map, unmapped, extras_seen = {}, [], []
    confidences = {}
    for pos, raw_h in enumerate(header_cells):
        if raw_h is None or (isinstance(raw_h, float) and pd.isna(raw_h)):
            continue
        raw_h_str = str(raw_h).strip()
        if not raw_h_str:
            continue
        field, conf = normalise_col(raw_h_str)
        confidences[raw_h_str] = conf
        if field.startswith(_EXTRA_PREFIX):
            unmapped.append(raw_h_str)
            extras_seen.append((pos, field, raw_h_str))
        else:
            # first mapped column for a field wins; later duplicates become extras
            if field not in col_map:
                col_map[field] = pos
            else:
                unmapped.append(raw_h_str)
                extras_seen.append((pos, field + "_dup", raw_h_str))

    # Position of each header, used to resolve a user override by header name.
    header_positions = {}
    for pos, raw_h in enumerate(header_cells):
        if raw_h is None or (isinstance(raw_h, float) and pd.isna(raw_h)):
            continue
        h = str(raw_h).strip()
        if h and h not in header_positions:
            header_positions[h] = pos

    # ── Manual overrides ────────────────────────────────────────────────
    # Automatic detection above is the default; anything the user corrected
    # in the mapping UI replaces it here. An override naming a header that
    # doesn't exist in this sheet is ignored, and an override set to ""
    # explicitly clears the field (e.g. "this sheet has no Completion Week").
    applied_overrides = {}
    for field, header in (overrides or {}).items():
        if field not in CANONICAL_FIELDS:
            continue
        if header in (None, ""):
            col_map.pop(field, None)
            applied_overrides[field] = ""
            continue
        if header in header_positions:
            col_map[field] = header_positions[header]
            applied_overrides[field] = header

    mapped_fields = set(col_map.keys())
    report["header_row_index"] = header_idx
    report["columns_detected"] = [str(c) for c in header_cells
                                   if c is not None and str(c).strip()]
    report["columns_mapped"] = {raw_h: normalise_col(raw_h)[0]
                                 for raw_h in report["columns_detected"]
                                 if not normalise_col(raw_h)[0].startswith(_EXTRA_PREFIX)}
    report["columns_unmapped"] = unmapped
    report["column_confidence"] = confidences
    report["applied_overrides"] = applied_overrides

    # Which Excel header each canonical field is actually reading from, and
    # how confident the automatic match was. Drives the mapping UI.
    pos_to_header = {pos: h for h, pos in header_positions.items()}
    report["field_mapping"] = {}
    for field, pos in col_map.items():
        header = pos_to_header.get(pos, "")
        report["field_mapping"][field] = {
            "column":     header,
            "confidence": 1.0 if field in applied_overrides
                          else confidences.get(header, 0.0),
            "overridden": field in applied_overrides,
        }

    # Which of the columns this importer needs could not be identified.
    # Surfaced so a failed import says "no Customer Name column found"
    # rather than just returning nothing.
    needed = {
        "customer_name": "Customer Name",
        "order_number":  "Order Number",
        "completion_week": "Completion Week",
        "inv_month":     "Invoice Month",
    }
    report["missing_required_columns"] = [
        label for field, label in needed.items() if field not in mapped_fields
    ]

    if not _is_order_sheet(mapped_fields):
        report["used"] = False
        report["skip_reason"] = "No Customer + Order/Value + period (Inv Month/Completion/Planned Week) columns found"
        return [], []

    report["used"] = True
    data_rows = raw_df.iloc[header_idx + 1:]
    report["rows_total"] = len(data_rows)

    records, skipped = [], []
    seen_order_numbers = set()
    seen_row_fingerprints = set()
    missing_order_numbers = 0

    for _, row in data_rows.iterrows():
        vals = row.tolist()

        def g(field):
            pos = col_map.get(field)
            if pos is None or pos >= len(vals):
                return ""
            return _format_cell(vals[pos])

        raw_row = {}
        for h_idx, cell in enumerate(vals):
            if h_idx < len(header_cells) and header_cells[h_idx] is not None:
                raw_row[str(header_cells[h_idx]).strip()] = _format_cell(cell)

        # skip fully blank rows silently (not a data-quality issue)
        if not any(v.strip() for v in raw_row.values() if isinstance(v, str)):
            continue

        cname = g("customer_name")
        onum  = g("order_number")

        # Customer name is the only truly required field — without it the
        # order cannot be grouped or mailed. A missing order number is
        # recorded as a data-quality note but never causes the row to be
        # discarded, since dropping otherwise-valid orders over one blank
        # cell is exactly the failure mode this importer must avoid.
        if not cname:
            skipped.append({"reasons": ["Missing customer name"],
                             "raw": raw_row, "sheet": sheet_name})
            continue
        if not onum:
            missing_order_numbers += 1

        # Duplicate handling — deliberately conservative. A repeated order
        # number is NOT enough to drop a row: many sheets legitimately list
        # the same order across several lines (split shipments, multiple
        # line items, part-quantities). Only a row that is byte-for-byte
        # identical to one already seen is a true duplicate and dropped.
        # Everything else is kept and merely flagged, so the row still
        # reaches the user instead of silently vanishing.
        row_fingerprint = tuple(sorted(raw_row.items()))
        if row_fingerprint in seen_row_fingerprints:
            skipped.append({"reasons": ["Duplicate row (identical to an earlier row)"],
                             "raw": raw_row, "sheet": sheet_name})
            continue
        seen_row_fingerprints.add(row_fingerprint)

        repeated_order_number = onum in seen_order_numbers
        seen_order_numbers.add(onum)

        record = {
            "order_number":     onum,
            "booked_date":      g("booked_date"),
            "branch":           g("branch"),
            "division":         g("division"),
            "customer_name":    cname,
            "customer_email":   g("customer_email"),
            "po_number":        g("po_number"),
            "value":            g("value"),
            "inv_month":        g("inv_month"),
            "planned_week":     g("planned_week"),
            "completion_week":  g("completion_week"),
            "spcl_cases":       g("spcl_cases"),
            "hold_details":     g("hold_details"),
            "sales_rep":        g("sales_rep"),
            "item_code":        g("item_code"),
            "shipping":         g("shipping"),
            "payment_terms":    g("payment_terms"),
            "request_date":     g("request_date"),
            "promise_date":     g("promise_date"),
            "schedule_ship_date": g("schedule_ship_date"),
            "oa_status":        g("oa_status"),
            "excel_ready_date": g("excel_ready_date"),
            "freight_terms":    g("freight_terms"),
            "inspection_agency": g("inspection_agency"),
            "bill_to_contact":  g("bill_to_contact"),
            "ready_date":       None,
            "ready_date_long":  "-",
            "order_status":     "pending",
            "_sheet":           sheet_name,
            "_raw":             raw_row,
            "_repeated_order_number": repeated_order_number,
        }
        records.append(record)

    report["rows_imported"] = len(records)
    report["rows_skipped"]  = len(skipped)
    report["rows_missing_order_number"] = missing_order_numbers
    return records, skipped


# ══════════════════════════════════════════════════════════════════════════
#  MONTH / YEAR AUTO-DETECTION
# ══════════════════════════════════════════════════════════════════════════

_MONTH_IN_TEXT_RE = re.compile(
    r"\b(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|"
    r"AUG(?:UST)?|SEP(?:T)?(?:EMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)\b",
    re.IGNORECASE,
)
_YEAR_IN_TEXT_RE = re.compile(r"(20\d{2})")
_SHORT_YEAR_RE   = re.compile(r"'?(\d{2})\b")


def _month_from_text_blob(text) -> tuple[int | None, int | None]:
    if not text:
        return (None, None)
    text = str(text).replace("_", " ").replace("-", " ")
    m = _MONTH_IN_TEXT_RE.search(text)
    if not m:
        return (None, None)
    token = m.group(1).upper()
    month_num = None
    for name, idx in _MONTH_NAME_TO_NUM.items():
        if name.startswith(token) or token.startswith(name[:3]):
            month_num = idx
            break
    if month_num is None:
        return (None, None)
    ym = _YEAR_IN_TEXT_RE.search(text)
    if ym:
        return (month_num, int(ym.group(1)))
    ys = _SHORT_YEAR_RE.search(text[m.end():])
    if ys:
        return (month_num, 2000 + int(ys.group(1)))
    return (month_num, None)


def _infer_year_from_dates(records: list) -> int | None:
    """Majority year across whatever date columns the sheet happens to have.
    Used only to fill in a year when the month is known but the year isn't
    stated anywhere — better than refusing to import."""
    votes = Counter()
    for r in records:
        for field in ("request_date", "promise_date", "schedule_ship_date", "booked_date"):
            m = re.match(r"^(\d{4})-\d{2}-\d{2}", str(r.get(field, "")))
            if m:
                votes[int(m.group(1))] += 1
    return votes.most_common(1)[0][0] if votes else None


def detect_month_year(records: list, filename: str, sheet_names: list) -> dict:
    """Best-effort auto-detection of the invoice month/year, in priority
    order: (1) majority of inv_month column values, (2) sheet name,
    (3) file name, (4) booked/request date columns. Returns a dict with
    the detected values and how confident/where they came from."""
    # 1) inv_month column majority
    month_year_votes = Counter()
    for r in records:
        mo, yr = extract_month_year(r.get("inv_month", ""))
        if mo:
            month_year_votes[(mo, yr)] += 1
    if month_year_votes:
        # merge votes that agree on month but differ only in a missing year
        by_month = Counter()
        year_for_month = defaultdict(Counter)
        for (mo, yr), cnt in month_year_votes.items():
            by_month[mo] += cnt
            if yr:
                year_for_month[mo][yr] += cnt
        top_month, top_count = by_month.most_common(1)[0]
        total_inv = sum(1 for r in records if r.get("inv_month"))
        if total_inv and top_count / total_inv >= 0.4:
            top_year = (year_for_month[top_month].most_common(1)[0][0]
                        if year_for_month[top_month] else None)
            source = "inv_month column"
            year_assumed = False
            # inv_month rarely carries a year — fall back to sheet name / file
            # name, then the sheet's date columns, then the current year,
            # without ever overriding the month we just established.
            if top_year is None:
                for sn in list(sheet_names) + [filename or ""]:
                    _, yr2 = _month_from_text_blob(sn)
                    if yr2:
                        top_year = yr2
                        source = "inv_month column (year from '%s')" % sn
                        break
            if top_year is None:
                top_year = _infer_year_from_dates(records)
                if top_year is not None:
                    source = "inv_month column (year from date columns)"
                    year_assumed = True
            if top_year is None:
                top_year = _dt.date.today().year
                source = "inv_month column (year assumed as current year)"
                year_assumed = True
            return {"month": top_month, "year": top_year,
                    "source": source, "confident": True,
                    "year_assumed": year_assumed}

    # 2) sheet name(s), 3) file name
    #
    # A month found here is a strong signal even when no year sits next to
    # it ("wm_testing_sep (2).xlsx" gives SEP but no year). Rather than
    # refusing to import, fill the year in from the sheet's own date
    # columns, falling back to the current year, and mark it as assumed so
    # the UI can ask the user to confirm rather than block them.
    for source_label, text in (
        *[(f"sheet name '{sn}'", sn) for sn in sheet_names],
        (f"file name '{filename}'", filename or ""),
    ):
        mo, yr = _month_from_text_blob(text)
        if not mo:
            continue
        if yr is not None:
            return {"month": mo, "year": yr, "source": source_label,
                     "confident": True, "year_assumed": False}
        inferred = _infer_year_from_dates(records)
        if inferred is not None:
            return {"month": mo, "year": inferred,
                     "source": f"{source_label}, year from date columns",
                     "confident": True, "year_assumed": True}
        return {"month": mo, "year": _dt.date.today().year,
                 "source": f"{source_label}, year assumed as current year",
                 "confident": True, "year_assumed": True}

    # 4) date-ish columns (booked_date / request_date) majority month
    date_votes = Counter()
    for r in records:
        for field in ("booked_date", "request_date"):
            v = r.get(field, "")
            m = re.match(r"^(\d{4})-(\d{2})-\d{2}", str(v))
            if m:
                date_votes[(int(m.group(2)), int(m.group(1)))] += 1
    if date_votes:
        (mo, yr), _ = date_votes.most_common(1)[0]
        return {"month": mo, "year": yr, "source": "date columns (low confidence)",
                 "confident": False}

    return {"month": None, "year": None, "source": "none", "confident": False}


# ══════════════════════════════════════════════════════════════════════════
#  TOP-LEVEL ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def run_import(file_bytes: bytes, filename: str,
               overrides: dict | None = None) -> dict:
    """Runs the full pipeline and returns a dict with:
        records          — imported canonical order records
        sheet_reports     — per-sheet diagnostics
        skipped_rows       — list of {reasons, raw, sheet}
        skip_reason_counts — Counter-like dict of reason -> count
        total_rows         — sum of rows_total across used sheets
        imported            — len(records)
        month_detection      — dict from detect_month_year()
        sheets_used / sheets_skipped
    """
    all_sheets = _read_all_sheets(file_bytes, filename)
    primary_sheets = _select_primary_sheets(list(all_sheets.keys()))

    records, skipped_rows, sheet_reports = [], [], []
    sheets_used, sheets_skipped = [], []

    for sheet_name, raw_df in all_sheets.items():
        report = {"sheet": sheet_name}

        if primary_sheets and sheet_name not in primary_sheets:
            # A "<Month> INV" sheet exists elsewhere in this workbook, so
            # this tab is not scanned as order data even if it structurally
            # qualifies — see _select_primary_sheets().
            report.update({
                "used": False,
                "skip_reason": (
                    "Not this period's invoicing sheet — only "
                    + ", ".join(f"'{s}'" for s in primary_sheets)
                    + " (matched by name) is scanned for orders this month."
                ),
                "rows_total": 0, "rows_imported": 0, "rows_skipped": 0,
                "rows_missing_order_number": 0,
                "header_row_index": None, "columns_detected": [],
                "columns_mapped": {}, "columns_unmapped": [],
                "missing_required_columns": [],
                "column_confidence": {}, "applied_overrides": {},
                "field_mapping": {},
            })
            sheet_reports.append(report)
            sheets_skipped.append(sheet_name)
            continue

        if raw_df is None or raw_df.empty:
            report.update({"used": False, "skip_reason": "Sheet is empty",
                            "rows_total": 0, "rows_imported": 0, "rows_skipped": 0,
                            "rows_missing_order_number": 0,
                            "header_row_index": None, "columns_detected": [],
                            "columns_mapped": {}, "columns_unmapped": [],
                            "missing_required_columns": [],
                            "column_confidence": {}, "applied_overrides": {},
                            "field_mapping": {}})
            sheet_reports.append(report)
            sheets_skipped.append(sheet_name)
            continue

        recs, skips = _parse_sheet(sheet_name, raw_df, report, overrides)
        sheet_reports.append(report)
        if report.get("used"):
            sheets_used.append(sheet_name)
            records.extend(recs)
            skipped_rows.extend(skips)
        else:
            sheets_skipped.append(sheet_name)

    skip_reason_counts = Counter()
    for s in skipped_rows:
        for reason in s["reasons"]:
            skip_reason_counts[reason] += 1

    total_rows = sum(r.get("rows_total", 0) for r in sheet_reports if r.get("used"))

    month_detection = detect_month_year(records, filename, list(all_sheets.keys()))

    if not records and not any(r.get("used") for r in sheet_reports):
        scanned = ", ".join(all_sheets.keys()) or "(no sheets found)"
        log.warning("run_import: no order sheet recognised. Scanned: %s", scanned)

    return {
        "records": records,
        "sheet_reports": sheet_reports,
        "skipped_rows": skipped_rows,
        "skip_reason_counts": dict(skip_reason_counts),
        "total_rows": total_rows,
        "imported": len(records),
        "month_detection": month_detection,
        "sheets_used": sheets_used,
        "sheets_skipped": sheets_skipped,
    }
