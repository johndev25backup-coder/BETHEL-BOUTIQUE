"""Google Sheets access helpers for Bethel Boutique."""
import json
import os
import threading

import gspread
from google.oauth2.service_account import Credentials

_client_lock = threading.Lock()
_spreadsheet = None


def get_spreadsheet():
    """Return the configured Bethel Boutique spreadsheet."""
    global _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured.")

    try:
        service_account = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

    if isinstance(service_account.get("private_key"), str):
        service_account["private_key"] = service_account["private_key"].replace("\\n", "\n")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(service_account, scopes=scopes)
    client = gspread.authorize(credentials)

    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    sheet_name = os.environ.get("GOOGLE_SHEET_NAME", "bethel_boutique.db").strip()

    with _client_lock:
        if _spreadsheet is None:
            _spreadsheet = client.open_by_key(sheet_id) if sheet_id else client.open(sheet_name)
    return _spreadsheet


def get_worksheet(name):
    return get_spreadsheet().worksheet(name)


def find_record_by(worksheet_name, field, value):
    """Find one record by a case-insensitive header and exact normalized value."""
    ws = get_worksheet(worksheet_name)
    values = ws.get_all_values()
    if not values:
        return None

    headers = [str(item).strip().lower() for item in values[0]]
    field_name = str(field).strip().lower()
    if field_name not in headers:
        raise RuntimeError(
            f"Column '{field}' was not found in worksheet '{worksheet_name}'."
        )

    field_index = headers.index(field_name)
    target = str(value).strip().lower() if field_name == "email" else str(value).strip()

    for row_number, row in enumerate(values[1:], start=2):
        padded = row + [""] * max(0, len(headers) - len(row))
        cell = str(padded[field_index]).strip()
        candidate = cell.lower() if field_name == "email" else cell
        if candidate == target:
            return {
                headers[i]: (padded[i] if i < len(padded) else "")
                for i in range(len(headers))
            } | {"_row_number": row_number}

    return None


def update_record_field(worksheet_name, row_number, field, value):
    """Update a single cell identified by its header name."""
    ws = get_worksheet(worksheet_name)
    headers = [str(item).strip().lower() for item in ws.row_values(1)]
    field_name = str(field).strip().lower()
    if field_name not in headers:
        raise RuntimeError(
            f"Column '{field}' was not found in worksheet '{worksheet_name}'."
        )
    ws.update_cell(int(row_number), headers.index(field_name) + 1, value)
