"""
In-memory dose state management.

All access is protected by a threading.Lock so it is safe to call
from both Flask request threads and APScheduler background threads.
"""
import threading
from datetime import datetime
from typing import Any

_lock = threading.Lock()

_state: dict[str, Any] = {
    "scheduled_time": None,
    "status": None,               # "pending" | "confirmed" | "missed" | "skipped"
    "confirmed_by": None,         # "Spencer" | "Peter" | None
    "confirmed_at": None,
    "administered_at": None,
    "note": None,
    "reminder_count": 0,
    "window_closed": False,       # True once the next dose window has opened
}

_follow_up_job_ids: list[str] = []


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get() -> dict[str, Any]:
    """Return a shallow copy of the current dose state."""
    with _lock:
        return dict(_state)


def get_follow_up_job_ids() -> list[str]:
    with _lock:
        return list(_follow_up_job_ids)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def new_window(scheduled_time: datetime) -> None:
    """Reset state for a brand-new dose window."""
    with _lock:
        _state.update({
            "scheduled_time": scheduled_time,
            "status": "pending",
            "confirmed_by": None,
            "confirmed_at": None,
            "administered_at": None,
            "note": None,
            "reminder_count": 0,
            "window_closed": False,
        })
        _follow_up_job_ids.clear()


def confirm(
    confirmed_by: str,
    administered_at: datetime,
    note: str | None,
    confirmed_at: datetime,
) -> None:
    with _lock:
        _state.update({
            "status": "confirmed",
            "confirmed_by": confirmed_by,
            "confirmed_at": confirmed_at,
            "administered_at": administered_at,
            "note": note,
        })


def skip(skipped_by: str, confirmed_at: datetime) -> None:
    with _lock:
        _state.update({
            "status": "skipped",
            "confirmed_by": skipped_by,
            "confirmed_at": confirmed_at,
        })


def mark_missed() -> None:
    with _lock:
        _state.update({
            "status": "missed",
            "window_closed": True,
        })


def close_window() -> None:
    """Mark the window as closed (called when the next dose window opens)."""
    with _lock:
        _state["window_closed"] = True


def increment_reminder() -> None:
    with _lock:
        _state["reminder_count"] += 1


def add_follow_up_job_id(job_id: str) -> None:
    with _lock:
        _follow_up_job_ids.append(job_id)


def clear_follow_up_job_ids() -> None:
    with _lock:
        _follow_up_job_ids.clear()


def restore(state_dict: dict[str, Any]) -> None:
    """Overwrite state from a dict (used on app startup to recover from Sheets)."""
    with _lock:
        _state.update(state_dict)
        _follow_up_job_ids.clear()
