"""
Google Sheets integration using a Service Account.

All public functions are wrapped in try/except and log errors without
raising, so a Sheets outage never crashes the app.
"""
import json
import logging
from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
_SHEET_NAME = "DoseLog"
_HEADERS = [
    "Scheduled Time",
    "Administered Time",
    "Confirmed By",
    "Confirmed At",
    "Status",
    "Note",
    "Reminder Count",
]

_worksheet: gspread.Worksheet | None = None


def _get_worksheet() -> gspread.Worksheet:
    global _worksheet
    if _worksheet is not None:
        return _worksheet

    service_account_info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(service_account_info, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)

    try:
        ws = spreadsheet.worksheet(_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=_SHEET_NAME, rows=1000, cols=len(_HEADERS))
        ws.append_row(_HEADERS)
        _worksheet = ws
        return ws

    # Ensure the header row exists
    existing = ws.row_values(1)
    if existing != _HEADERS:
        ws.insert_row(_HEADERS, 1)

    _worksheet = ws
    return ws


def _fmt(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def append_dose_log(dose_state: dict[str, Any]) -> bool:
    """Append one row to DoseLog for the given dose state."""
    try:
        ws = _get_worksheet()
        row = [
            _fmt(dose_state.get("scheduled_time")),
            _fmt(dose_state.get("administered_at")),
            dose_state.get("confirmed_by") or "",
            _fmt(dose_state.get("confirmed_at")),
            dose_state.get("status") or "",
            dose_state.get("note") or "",
            str(dose_state.get("reminder_count", 0)),
        ]
        ws.append_row(row)
        logger.info("Appended dose log: status=%s scheduled=%s", dose_state.get("status"), dose_state.get("scheduled_time"))
        return True
    except Exception as exc:
        logger.error("Failed to append to Google Sheets: %s", exc)
        return False


def get_recent_logs(n: int = 10) -> list[dict[str, str]]:
    """Return the last n rows as a list of dicts (most recent first)."""
    try:
        ws = _get_worksheet()
        records = ws.get_all_records()
        recent = records[-n:] if len(records) > n else records
        return list(reversed(recent))
    except Exception as exc:
        logger.error("Failed to read recent logs from Sheets: %s", exc)
        return []


def get_last_dose() -> dict[str, str] | None:
    """Return the last logged dose row as a dict, or None."""
    try:
        ws = _get_worksheet()
        records = ws.get_all_records()
        if not records:
            return None
        return records[-1]
    except Exception as exc:
        logger.error("Failed to read last dose from Sheets: %s", exc)
        return None
