"""
Twilio SMS helpers and message templates.

All send_* calls are wrapped in try/except so a Twilio failure never
crashes the scheduler or webhook.
"""
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

import config

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _client


def send_sms(to: str, body: str) -> bool:
    """Send an SMS. Returns True on success, False on failure."""
    try:
        _get_client().messages.create(
            body=body,
            from_=config.TWILIO_PHONE_NUMBER,
            to=to,
        )
        logger.info("SMS sent to %s: %.60s", to, body)
        return True
    except TwilioRestException as exc:
        logger.error("Twilio error sending to %s: %s", to, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error sending SMS to %s: %s", to, exc)
        return False


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

def initial_reminder_msg(med_name: str) -> str:
    return (
        f"💊 Time to give Gio his {med_name}! "
        "Reply YES to confirm, or YES [time] or YES [time] [note] for details."
    )


def follow_up_reminder_msg(med_name: str) -> str:
    return f"⏰ Reminder: Gio's {med_name} hasn't been confirmed yet. Reply YES to confirm."


def confirmation_ack_msg(med_name: str, time_str: str) -> str:
    return f"✅ Got it! Logged Gio's {med_name} at {time_str}."


def cross_notification_msg(name: str, med_name: str, time_str: str, note: str | None = None) -> str:
    note_suffix = f" Note: {note}" if note else ""
    return f"✅ {name} gave Gio his {med_name} at {time_str}.{note_suffix}"


def missed_dose_msg(scheduled_time_str: str, med_name: str) -> str:
    return (
        f"⚠️ Gio's {scheduled_time_str} {med_name} window has closed without confirmation. "
        "Dose logged as missed."
    )


def skip_ack_msg(med_name: str) -> str:
    return f"📋 Logged. Gio's {med_name} dose marked as skipped."


def skip_cross_notification_msg(name: str, scheduled_time_str: str, med_name: str) -> str:
    return f"📋 {name} marked Gio's {scheduled_time_str} {med_name} dose as skipped."


def unrecognized_msg() -> str:
    return "Hmm, didn't catch that. Reply YES to confirm Gio's dose, or NO/SKIP to mark as skipped."


def already_logged_msg(med_name: str, time_str: str, status: str, confirmed_by: str) -> str:
    return (
        f"Gio's {time_str} {med_name} dose was already logged as {status} "
        f"by {confirmed_by}. Nothing to do!"
    )


def no_active_window_msg() -> str:
    return "No active dose window right now. Nothing to confirm!"
