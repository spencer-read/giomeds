"""
GioMeds Flask application.

Provides:
  POST /sms/incoming        — Twilio webhook for incoming SMS replies
  GET/POST /admin/login     — Admin login
  GET /admin/logout         — Admin logout
  GET /admin                — Dashboard (current status + recent logs)
  GET/POST /admin/schedule  — Edit medication schedule
  GET/POST /admin/vacation  — Configure vacation mode
"""
import logging
from datetime import datetime, timedelta
from functools import wraps

import pytz
from flask import (
    Flask, abort, flash, redirect, render_template,
    request, session, url_for,
)
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import config
import parser
import scheduler as sched
import sheets
import sms
import state
import vacation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.ADMIN_SECRET_KEY


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == config.ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect password.")
    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@login_required
def admin_dashboard():
    schedule = sched.load_schedule()
    current_dose = state.get()
    recent_logs = sheets.get_recent_logs(10)
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    vac_data = vacation.get_vacation()
    vac_is_active = vacation.vacation_active()
    return render_template(
        "admin.html",
        schedule=schedule,
        current_dose=current_dose,
        recent_logs=recent_logs,
        now=now,
        timezone=config.TIMEZONE,
        vacation=vac_data,
        vacation_is_active=vac_is_active,
    )


@app.route("/admin/schedule", methods=["GET", "POST"])
@login_required
def admin_schedule():
    if request.method == "POST":
        med_name = request.form.get("medication_name", "").strip()
        timezone = request.form.get("timezone", config.TIMEZONE).strip()
        dose_times = [t.strip() for t in request.form.getlist("dose_times") if t.strip()]

        if not med_name:
            flash("Medication name is required.")
            return redirect(url_for("admin_schedule"))
        if not dose_times:
            flash("At least one dose time is required.")
            return redirect(url_for("admin_schedule"))

        new_schedule = {
            "medication_name": med_name,
            "dose_times": sorted(dose_times),
            "timezone": timezone,
        }
        sched.save_schedule(new_schedule)
        sched.reschedule_all_jobs()
        flash("Schedule saved and jobs rescheduled successfully.")
        return redirect(url_for("admin_dashboard"))

    schedule = sched.load_schedule()
    return render_template("admin_schedule.html", schedule=schedule)


@app.route("/admin/vacation", methods=["GET", "POST"])
@login_required
def admin_vacation():
    if request.method == "POST":
        # "Clear" button resets to defaults
        if "clear_vacation" in request.form:
            vacation.save_vacation({
                "active": False,
                "sitter_name": "Sitter",
                "sitter_phone": "",
                "start": "",
                "end": "",
                "suspend_owner_notifications": False,
            })
            flash("Vacation mode cleared.")
            return redirect(url_for("admin_dashboard"))

        active = "active" in request.form
        sitter_name_val = request.form.get("sitter_name", "").strip()
        sitter_phone_val = request.form.get("sitter_phone", "").strip()
        start_val = request.form.get("start", "").strip()
        end_val = request.form.get("end", "").strip()
        suspend = "suspend_owner_notifications" in request.form

        # Validation
        if active and not sitter_phone_val:
            flash("Sitter phone number is required when vacation mode is enabled.")
            return redirect(url_for("admin_vacation"))
        if active and sitter_phone_val and not sitter_phone_val.startswith("+"):
            flash("Sitter phone must be in E.164 format (starting with +).")
            return redirect(url_for("admin_vacation"))
        if start_val and end_val and end_val <= start_val:
            flash("End date/time must be after start date/time.")
            return redirect(url_for("admin_vacation"))

        vacation.save_vacation({
            "active": active,
            "sitter_name": sitter_name_val or "Sitter",
            "sitter_phone": sitter_phone_val,
            "start": start_val,
            "end": end_val,
            "suspend_owner_notifications": suspend,
        })
        flash("Vacation mode settings saved.")
        return redirect(url_for("admin_dashboard"))

    vac_data = vacation.get_vacation()
    return render_template("admin_vacation.html", vacation=vac_data)


# ---------------------------------------------------------------------------
# Twilio SMS webhook
# ---------------------------------------------------------------------------

def _request_url() -> str:
    """Reconstruct the full URL, honouring X-Forwarded-Proto from Railway/Render."""
    url = request.url
    proto = request.headers.get("X-Forwarded-Proto", "")
    if proto == "https" and url.startswith("http://"):
        url = "https://" + url[7:]
    return url


@app.route("/sms/incoming", methods=["POST"])
def sms_incoming():
    # --- Validate Twilio signature ---
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = _request_url()
    params = request.form.to_dict()

    if not validator.validate(url, params, signature):
        logger.warning("Invalid Twilio signature from %s; rejecting", request.remote_addr)
        abort(403)

    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()
    resp = MessagingResponse()  # empty TwiML — we send outbound replies ourselves

    # --- Identify sender ---
    sender_name = config.USERS.get(from_number)
    is_sitter = False
    if not sender_name:
        # Check if this is the sitter during an active vacation window
        if vacation.vacation_active() and from_number == vacation.sitter_phone():
            sender_name = vacation.sitter_name()
            is_sitter = True
        else:
            logger.info("Unknown sender %s; ignoring silently", from_number)
            return str(resp), 200, {"Content-Type": "text/xml"}

    logger.info("Incoming SMS from %s (%s, sitter=%s): %s", sender_name, from_number, is_sitter, body)

    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    schedule = sched.load_schedule()
    med_name = schedule["medication_name"]
    current = state.get()

    # --- Guard: no active window ---
    if not current.get("status"):
        sms.send_sms(from_number, sms.no_active_window_msg())
        return str(resp), 200, {"Content-Type": "text/xml"}

    # --- Guard: window already closed (missed) ---
    if current.get("window_closed") and current.get("status") == "missed":
        sms.send_sms(from_number, sms.no_active_window_msg())
        return str(resp), 200, {"Content-Type": "text/xml"}

    # --- Guard: already confirmed or skipped ---
    if current.get("status") in ("confirmed", "skipped"):
        scheduled_time = current.get("scheduled_time")
        time_str = scheduled_time.strftime("%H:%M") if scheduled_time else "unknown"
        sms.send_sms(from_number, sms.already_logged_msg(
            med_name=med_name,
            time_str=time_str,
            status=current["status"],
            confirmed_by=current.get("confirmed_by") or "someone",
        ))
        return str(resp), 200, {"Content-Type": "text/xml"}

    # --- Parse reply ---
    parsed = parser.parse(body, now)
    scheduled_time = current.get("scheduled_time")
    sched_time_str = scheduled_time.strftime("%H:%M") if scheduled_time else "unknown"

    # Vacation-aware cross-notification targets
    vac_active = vacation.vacation_active()
    vac_suspended = vacation.owners_suspended() if vac_active else False

    if parsed["action"] == "confirm":
        administered_at = parsed["administered_at"] or now
        note = parsed["note"]
        admin_time_str = administered_at.strftime("%H:%M")

        state.confirm(
            confirmed_by=sender_name,
            administered_at=administered_at,
            note=note,
            confirmed_at=now,
        )
        sheets.append_dose_log(state.get())
        sched.cancel_follow_up_jobs()

        sms.send_sms(from_number, sms.confirmation_ack_msg(med_name, admin_time_str))

        cross_msg = sms.cross_notification_msg(
            name=sender_name,
            med_name=med_name,
            time_str=admin_time_str,
            note=note,
        )
        if is_sitter:
            # Sitter confirmed — notify owners only if not suspended
            if not vac_suspended:
                sms.send_sms(config.SPENCER_PHONE, cross_msg)
                sms.send_sms(config.PETER_PHONE, cross_msg)
        else:
            # Owner confirmed — notify the other owner, plus sitter if vacation active
            _, other_phone = config.get_other_user(from_number)
            if other_phone:
                sms.send_sms(other_phone, cross_msg)
            if vac_active:
                sitter_ph = vacation.sitter_phone()
                if sitter_ph:
                    sms.send_sms(sitter_ph, cross_msg)

    elif parsed["action"] == "skip":
        state.skip(skipped_by=sender_name, confirmed_at=now)
        sheets.append_dose_log(state.get())
        sched.cancel_follow_up_jobs()

        sms.send_sms(from_number, sms.skip_ack_msg(med_name))

        skip_cross_msg = sms.skip_cross_notification_msg(
            name=sender_name,
            scheduled_time_str=sched_time_str,
            med_name=med_name,
        )
        if is_sitter:
            # Sitter skipped — notify owners only if not suspended
            if not vac_suspended:
                sms.send_sms(config.SPENCER_PHONE, skip_cross_msg)
                sms.send_sms(config.PETER_PHONE, skip_cross_msg)
        else:
            # Owner skipped — notify the other owner, plus sitter if vacation active
            _, other_phone = config.get_other_user(from_number)
            if other_phone:
                sms.send_sms(other_phone, skip_cross_msg)
            if vac_active:
                sitter_ph = vacation.sitter_phone()
                if sitter_ph:
                    sms.send_sms(sitter_ph, skip_cross_msg)

    else:  # unknown
        sms.send_sms(from_number, sms.unrecognized_msg())

    return str(resp), 200, {"Content-Type": "text/xml"}


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

def _restore_state_from_sheets() -> None:
    """
    On startup, check Google Sheets for a log entry that matches the current
    dose window.  If found, restore that state; otherwise seed a pending state
    for the current window (if we are currently inside one).
    """
    try:
        tz = pytz.timezone(config.TIMEZONE)
        now = datetime.now(tz)
        schedule = sched.load_schedule()
        dose_times = schedule.get("dose_times", [])

        if not dose_times:
            return

        # Find the most recent dose time that has already passed today
        current_window_start: datetime | None = None
        for time_str in dose_times:
            h, m = map(int, time_str.split(":"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate > now:
                candidate -= timedelta(days=1)
            if current_window_start is None or candidate > current_window_start:
                current_window_start = candidate

        if current_window_start is None:
            return

        # Check Sheets for a matching log entry
        last_dose = sheets.get_last_dose()
        if last_dose and last_dose.get("Scheduled Time"):
            try:
                logged_dt = datetime.fromisoformat(last_dose["Scheduled Time"])
                if logged_dt.tzinfo is None:
                    logged_dt = tz.localize(logged_dt)

                # Consider it a match if scheduled times are within 5 minutes
                if abs((logged_dt - current_window_start).total_seconds()) < 300:
                    status = last_dose.get("Status", "pending")
                    confirmed_by = last_dose.get("Confirmed By") or None
                    note = last_dose.get("Note") or None
                    reminder_count = int(last_dose.get("Reminder Count") or 0)

                    def _parse_dt(s: str) -> datetime | None:
                        if not s:
                            return None
                        try:
                            dt = datetime.fromisoformat(s)
                            return tz.localize(dt) if dt.tzinfo is None else dt
                        except ValueError:
                            return None

                    restored = {
                        "scheduled_time": logged_dt,
                        "status": status,
                        "confirmed_by": confirmed_by,
                        "confirmed_at": _parse_dt(last_dose.get("Confirmed At", "")),
                        "administered_at": _parse_dt(last_dose.get("Administered Time", "")),
                        "note": note,
                        "reminder_count": reminder_count,
                        "window_closed": status in ("missed",),
                    }
                    state.restore(restored)
                    logger.info("Restored dose state from Sheets: %s for %s", status, logged_dt)
                    return
            except (ValueError, TypeError) as exc:
                logger.warning("Could not parse last Sheets row: %s", exc)

        # No matching row — set up a pending state for the current window
        state.new_window(current_window_start)
        logger.info("No Sheets match; initialised pending state for window starting %s", current_window_start)

    except Exception as exc:
        logger.error("Failed to restore state from Sheets: %s", exc)


# Run once at import time (before Gunicorn forks workers, or when Flask dev-server loads)
_restore_state_from_sheets()
sched.init_scheduler()


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
