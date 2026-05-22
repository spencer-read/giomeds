"""
Vacation mode state management.

Reads/writes vacation.json and exposes helper functions used by the
scheduler and SMS webhook to route notifications appropriately.
"""
import json
import logging
import os
from datetime import datetime

import pytz

import config

logger = logging.getLogger(__name__)

VACATION_FILE = os.path.join(os.path.dirname(__file__), "vacation.json")

_DEFAULTS: dict = {
    "active": False,
    "sitter_name": "Sitter",
    "sitter_phone": "",
    "start": "",
    "end": "",
    "suspend_owner_notifications": False,
}


def normalize_phone(number: str) -> str:
    """Return a comparable E.164 form of a UK phone number.

    Handles the common mistake of '+44' followed by a leading trunk '0',
    e.g. '+4407827334799' -> '+447827334799'.
    """
    if not number:
        return ""
    n = number.strip().replace(" ", "")
    if n.startswith("+440"):
        n = "+44" + n[4:]
    return n


def get_vacation() -> dict:
    """Return the parsed vacation.json, or empty defaults if missing/corrupt."""
    if os.path.exists(VACATION_FILE):
        try:
            with open(VACATION_FILE) as f:
                return json.load(f)
        except Exception as exc:
            logger.error("Failed to load vacation.json: %s", exc)
    return dict(_DEFAULTS)


def save_vacation(data: dict) -> None:
    """Write vacation state to vacation.json."""
    with open(VACATION_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("vacation.json saved (active=%s)", data.get("active"))


def _parse_window_dt(dt_str: str) -> datetime | None:
    """Parse an ISO 8601 string (possibly naive) into a timezone-aware datetime."""
    if not dt_str:
        return None
    try:
        tz = pytz.timezone(config.TIMEZONE)
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        return dt
    except (ValueError, TypeError) as exc:
        logger.warning("Could not parse vacation datetime '%s': %s", dt_str, exc)
        return None


def vacation_active() -> bool:
    """Return True if the current datetime falls within the configured vacation window
    and `active` is True."""
    data = get_vacation()
    if not data.get("active"):
        return False

    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)

    start = _parse_window_dt(data.get("start", ""))
    end = _parse_window_dt(data.get("end", ""))

    if not start or not end:
        return False

    return start <= now <= end


def sitter_phone() -> str | None:
    """Return the sitter's phone number if vacation is active, else None."""
    if not vacation_active():
        return None
    return get_vacation().get("sitter_phone") or None


def sitter_name() -> str:
    """Return the sitter's display name if vacation is active, else 'Sitter'."""
    if not vacation_active():
        return "Sitter"
    return get_vacation().get("sitter_name") or "Sitter"


def owners_suspended() -> bool:
    """Return True if owner notifications are suspended during an active vacation."""
    if not vacation_active():
        return False
    return bool(get_vacation().get("suspend_owner_notifications", False))


def normalize_stored_sitter_phone() -> None:
    """One-time cleanup: rewrite vacation.json if the stored sitter phone
    is not already in normalized E.164 form."""
    data = get_vacation()
    stored = data.get("sitter_phone", "")
    if not stored:
        return
    normalized = normalize_phone(stored)
    if normalized != stored:
        data["sitter_phone"] = normalized
        save_vacation(data)
        logger.info("Normalized stored sitter_phone: '%s' -> '%s'", stored, normalized)
