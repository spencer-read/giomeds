"""
Loads and validates all environment variables required by GioMeds.
Raises EnvironmentError at import time if any required variable is missing.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"Required environment variable '{key}' is not set")
    return val


TWILIO_ACCOUNT_SID = _require("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _require("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _require("TWILIO_PHONE_NUMBER")

SPENCER_PHONE = _require("SPENCER_PHONE")
PETER_PHONE = _require("PETER_PHONE")

GOOGLE_SERVICE_ACCOUNT_JSON = _require("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = _require("GOOGLE_SHEET_ID")

ADMIN_PASSWORD = _require("ADMIN_PASSWORD")
ADMIN_SECRET_KEY = _require("ADMIN_SECRET_KEY")

TIMEZONE = os.environ.get("TIMEZONE", "Europe/London")

USERS: dict[str, str] = {
    SPENCER_PHONE: "Spencer",
    PETER_PHONE: "Peter",
}


def get_other_user(phone: str) -> tuple[str | None, str | None]:
    """Returns (name, phone) of the person who is NOT the given phone number."""
    if phone == SPENCER_PHONE:
        return "Peter", PETER_PHONE
    elif phone == PETER_PHONE:
        return "Spencer", SPENCER_PHONE
    return None, None
