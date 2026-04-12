"""
APScheduler job definitions and schedule management.

Uses a single BackgroundScheduler (one instance per process) with:
  - CronTrigger jobs for each daily dose time
  - DateTrigger jobs for follow-up reminders (chained, 60-min intervals, max 2)

Job lifecycle:
  dose_window_job(time_str)
    → closes previous pending window (missed)
    → opens new window (pending)
    → sends initial reminder to reminder recipients
    → schedules follow_up_reminder_job in REMINDER_INTERVAL_MINUTES

  follow_up_reminder_job()
    → if still pending and reminder_count < MAX_REMINDERS:
        sends reminder, reschedules itself in REMINDER_INTERVAL_MINUTES
      (also skips scheduling if the next dose time would fire first)
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import config
import state
import sms
import sheets
import vacation

logger = logging.getLogger(__name__)

SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "schedule.json")

_DEFAULT_SCHEDULE: dict[str, Any] = {
    "medication_name": "Phenobarbital",
    "dose_times": ["08:00", "20:00"],
    "timezone": "Europe/London",
}

scheduler = BackgroundScheduler(timezone=config.TIMEZONE)

# ---------------------------------------------------------------------------
# Reminder constants — adjust here to change interval or cap
# ---------------------------------------------------------------------------
REMINDER_INTERVAL_MINUTES = 60  # Minutes between follow-up reminders
MAX_REMINDERS = 2               # Maximum follow-up reminders per dose window


# ---------------------------------------------------------------------------
# Schedule file helpers
# ---------------------------------------------------------------------------

def load_schedule() -> dict[str, Any]:
    if os.path.exists(SCHEDULE_FILE):
        try:
            with open(SCHEDULE_FILE) as f:
                return json.load(f)
        except Exception as exc:
            logger.error("Failed to load %s: %s", SCHEDULE_FILE, exc)
    return dict(_DEFAULT_SCHEDULE)


def save_schedule(schedule: dict[str, Any]) -> None:
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(schedule, f, indent=2)
    logger.info("Schedule saved to %s", SCHEDULE_FILE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tz() -> pytz.BaseTzInfo:
    return pytz.timezone(config.TIMEZONE)


def _now() -> datetime:
    return datetime.now(_tz())


def _next_dose_after(current_time: datetime, dose_times: list[str]) -> datetime | None:
    """Return the earliest scheduled dose datetime strictly after current_time."""
    tz = _tz()
    if current_time.tzinfo is None:
        current_time = tz.localize(current_time)

    candidates = []
    for time_str in dose_times:
        h, m = map(int, time_str.split(":"))
        candidate = current_time.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= current_time:
            candidate += timedelta(days=1)
        candidates.append(candidate)

    return min(candidates) if candidates else None


def _reminder_recipients() -> list[str]:
    """Return the list of phone numbers to receive reminder/alert SMS.

    During an active vacation window:
      - Always includes the sitter.
      - Includes owners only if suspend_owner_notifications is False.
    Outside vacation: always Spencer and Peter.
    """
    if vacation.vacation_active():
        phones: list[str] = []
        sitter = vacation.sitter_phone()
        if sitter:
            phones.append(sitter)
        if not vacation.owners_suspended():
            phones += [config.SPENCER_PHONE, config.PETER_PHONE]
        return phones
    return [config.SPENCER_PHONE, config.PETER_PHONE]


def cancel_follow_up_jobs() -> None:
    """Cancel all outstanding follow-up reminder jobs and clear the ID list."""
    for job_id in state.get_follow_up_job_ids():
        try:
            scheduler.remove_job(job_id)
            logger.debug("Cancelled follow-up job %s", job_id)
        except Exception:
            pass  # Already fired or never added — ignore
    state.clear_follow_up_job_ids()


def _schedule_follow_up(delay_minutes: int = REMINDER_INTERVAL_MINUTES) -> None:
    """Schedule one follow-up reminder `delay_minutes` from now.

    Skips scheduling if the next dose window would start before the follow-up
    would fire (no point sending a reminder when the cron job is about to reset
    the window anyway).
    """
    now = _now()
    schedule = load_schedule()
    next_dose = _next_dose_after(now, schedule["dose_times"])

    run_at = now + timedelta(minutes=delay_minutes)

    if next_dose and next_dose <= run_at:
        logger.info("Next dose at %s is before follow-up at %s; skipping follow-up", next_dose, run_at)
        return

    job_id = f"follow_up_{run_at.strftime('%Y%m%dT%H%M%S')}"
    try:
        scheduler.add_job(
            follow_up_reminder_job,
            trigger=DateTrigger(run_date=run_at, timezone=config.TIMEZONE),
            id=job_id,
            replace_existing=True,
        )
        state.add_follow_up_job_id(job_id)
        logger.info("Scheduled follow-up reminder at %s (job %s)", run_at, job_id)
    except Exception as exc:
        logger.error("Failed to schedule follow-up job: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler jobs
# ---------------------------------------------------------------------------

def dose_window_job(time_str: str) -> None:
    """Fires at each scheduled dose time (CronTrigger, daily).

    1. Closes the previous window if still pending (missed dose).
    2. Opens the new window.
    3. Sends the initial reminder to all recipients.
    4. Schedules a follow-up in REMINDER_INTERVAL_MINUTES.
    """
    now = _now()
    schedule = load_schedule()
    med_name = schedule["medication_name"]

    logger.info("Dose window job fired for %s", time_str)

    # Close previous window if still pending
    current = state.get()
    if current.get("status") == "pending":
        logger.info("Previous window still pending — marking as missed")
        state.mark_missed()
        sheets.append_dose_log(state.get())
        scheduled_time = current.get("scheduled_time")
        sched_str = scheduled_time.strftime("%H:%M") if scheduled_time else time_str
        missed_msg = sms.missed_dose_msg(sched_str, med_name)
        for phone in _reminder_recipients():
            sms.send_sms(phone, missed_msg)

    # Cancel any outstanding follow-up jobs
    cancel_follow_up_jobs()

    # Open new window
    h, m = map(int, time_str.split(":"))
    scheduled_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
    state.new_window(scheduled_time)

    # Send initial reminder
    reminder = sms.initial_reminder_msg(med_name)
    for phone in _reminder_recipients():
        sms.send_sms(phone, reminder)

    # Schedule first follow-up
    _schedule_follow_up(delay_minutes=REMINDER_INTERVAL_MINUTES)


def follow_up_reminder_job() -> None:
    """Follow-up reminder — fires every REMINDER_INTERVAL_MINUTES while dose is pending.
    Stops after MAX_REMINDERS follow-ups have been sent."""
    current = state.get()

    if current.get("status") != "pending":
        logger.info("Follow-up fired but dose is no longer pending; skipping")
        return

    schedule = load_schedule()
    med_name = schedule["medication_name"]

    state.increment_reminder()
    follow_up = sms.follow_up_reminder_msg(med_name)
    for phone in _reminder_recipients():
        sms.send_sms(phone, follow_up)

    updated = state.get()
    count = updated.get("reminder_count", 0)
    logger.info("Sent follow-up reminder #%d", count)

    # Chain the next follow-up only if the cap has not been reached
    if count < MAX_REMINDERS:
        _schedule_follow_up(delay_minutes=REMINDER_INTERVAL_MINUTES)
    else:
        logger.info("Reminder cap (%d) reached; no further follow-ups for this window", MAX_REMINDERS)


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

def reschedule_all_jobs() -> None:
    """Remove all existing dose-window CronTrigger jobs and rebuild from schedule.json."""
    schedule = load_schedule()
    dose_times: list[str] = schedule.get("dose_times", [])
    tz_str: str = schedule.get("timezone", config.TIMEZONE)

    # Remove old dose window jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("dose_window_"):
            try:
                scheduler.remove_job(job.id)
            except Exception:
                pass

    # Add new dose window jobs
    for time_str in dose_times:
        h, m = map(int, time_str.split(":"))
        job_id = f"dose_window_{time_str.replace(':', '')}"
        scheduler.add_job(
            dose_window_job,
            trigger=CronTrigger(hour=h, minute=m, timezone=tz_str),
            id=job_id,
            args=[time_str],
            replace_existing=True,
        )
        logger.info("Scheduled dose window job for %s (%s)", time_str, tz_str)


def init_scheduler() -> None:
    """Start the scheduler and load the initial schedule."""
    if not scheduler.running:
        scheduler.start()
    reschedule_all_jobs()
    logger.info("Scheduler initialised with %d jobs", len(scheduler.get_jobs()))
