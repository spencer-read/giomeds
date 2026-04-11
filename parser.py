"""
Parses incoming SMS reply bodies into structured actions.

Accepted formats (all case-insensitive, whitespace-tolerant):
  YES / Y                       → confirm, time=now, no note
  YES 7:45                      → confirm, administered_at=7:45 (inferred AM/PM)
  YES 7:45pm                    → confirm, administered_at=7:45 PM
  YES gave with food            → confirm, time=now, note="gave with food"
  YES 7:45 gave with food       → confirm, administered_at=7:45, note="gave with food"
  NO / N / SKIP                 → skip
  anything else                 → unknown
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _parse_time(time_str: str, now: datetime) -> datetime | None:
    """
    Parse a time string such as '7:45', '19:00', '7:45pm'.
    Returns a timezone-aware datetime (same tz as `now`), or None if parsing
    fails or the result is rejected.

    Rules:
    - With explicit AM/PM: trust it; reject if result is in the future.
    - Without AM/PM: accept only if the time falls within the 3 hours before now;
      also try the 12-hours-offset variant; otherwise return None (default to now).
    - If the parsed time is in the future: reject (return None).
    """
    time_str = time_str.strip().lower()
    match = re.match(r'^(\d{1,2}):(\d{2})\s*(am|pm)?$', time_str)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3)

    if ampm == 'pm' and hour != 12:
        hour += 12
    elif ampm == 'am' and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        return None

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if ampm:
        # Explicit AM/PM — trust it, but reject future times
        if candidate > now:
            logger.debug("Parsed time %s is in the future; defaulting to now", time_str)
            return None
        return candidate

    # No AM/PM — apply inference rules
    if candidate <= now and (now - candidate) <= timedelta(hours=3):
        return candidate

    # Try PM offset (add 12 hours)
    pm_candidate = candidate.replace(hour=(hour + 12) % 24)
    if pm_candidate <= now and (now - pm_candidate) <= timedelta(hours=3):
        return pm_candidate

    logger.debug("Time %s is ambiguous; defaulting to now", time_str)
    return None


def parse(body: str, now: datetime) -> dict[str, Any]:
    """
    Parse an incoming SMS reply body.

    Returns:
        {
          "action": "confirm" | "skip" | "unknown",
          "administered_at": datetime | None,
          "note": str | None,
        }
    """
    body = body.strip()

    # --- Skip variants ---
    if re.match(r'^(no|n|skip)\s*$', body, re.IGNORECASE):
        return {"action": "skip", "administered_at": None, "note": None}

    # --- YES variants ---
    yes_match = re.match(r'^(yes|y)\b\s*(.*)?$', body, re.IGNORECASE)
    if not yes_match:
        return {"action": "unknown", "administered_at": None, "note": None}

    remainder = (yes_match.group(2) or "").strip()

    if not remainder:
        return {"action": "confirm", "administered_at": now, "note": None}

    # Try to pull an optional leading time token: H:MM or HH:MM with optional am/pm
    time_match = re.match(r'^(\d{1,2}:\d{2}\s*(?:am|pm)?)\s*(.*)?$', remainder, re.IGNORECASE)
    if time_match:
        time_str = time_match.group(1).strip()
        note = (time_match.group(2) or "").strip() or None
        parsed_time = _parse_time(time_str, now)
        administered_at = parsed_time if parsed_time is not None else now
        return {"action": "confirm", "administered_at": administered_at, "note": note}

    # No time token — treat the entire remainder as a note
    return {"action": "confirm", "administered_at": now, "note": remainder or None}
