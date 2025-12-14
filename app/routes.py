import threading
from datetime import datetime

# ---------------------------------------------------------
# Simple in-memory logger + progress tracker (thread-safe)
# ---------------------------------------------------------

_LOCK = threading.Lock()
_LOGS = []  # list[str]

_PROGRESS = {
    "status": "IDLE",
    "percent": 0,
    "current_step": "",
    "details": {},
}

_MAX_LOG_LINES = 2000


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(message: str) -> None:
    """Append a log line with a timestamp."""
    if message is None:
        return
    line = str(message)
    if not line.startswith("["):
        line = f"[{_ts()}] {line}"
    with _LOCK:
        _LOGS.append(line)
        # cap memory
        if len(_LOGS) > _MAX_LOG_LINES:
            del _LOGS[: len(_LOGS) - _MAX_LOG_LINES]


def clear_logs() -> None:
    with _LOCK:
        _LOGS.clear()


def get_logs() -> list[str]:
    with _LOCK:
        return list(_LOGS)


def reset_progress() -> None:
    """Reset progress back to a clean idle state."""
    with _LOCK:
        _PROGRESS["status"] = "IDLE"
        _PROGRESS["percent"] = 0
        _PROGRESS["current_step"] = ""
        _PROGRESS["details"] = {}


def set_progress(**kwargs) -> None:
    """
    Flexible progress setter.

    Accepts multiple legacy keyword shapes used across the repo:
      - percent / percentage
      - status
      - current_step
      - details (dict)
      - total_files / current_file (optional)
    Unknown keywords are ignored on purpose (to avoid breaking callers).
    """
    with _LOCK:
        # status
        if "status" in kwargs and kwargs["status"] is not None:
            _PROGRESS["status"] = str(kwargs["status"]).upper()

        # step text
        if "current_step" in kwargs and kwargs["current_step"] is not None:
            _PROGRESS["current_step"] = str(kwargs["current_step"])

        # details merge
        if "details" in kwargs and isinstance(kwargs["details"], dict):
            _PROGRESS.setdefault("details", {})
            _PROGRESS["details"].update(kwargs["details"])

        # percent / percentage
        pct = None
        if "percent" in kwargs and kwargs["percent"] is not None:
            pct = kwargs["percent"]
        elif "percentage" in kwargs and kwargs["percentage"] is not None:
            pct = kwargs["percentage"]

        if pct is None:
            # optionally compute from file counters (if provided)
            try:
                tf = kwargs.get("total_files")
                cf = kwargs.get("current_file")
                if tf is not None and cf is not None and int(tf) > 0:
                    pct = int((int(cf) / int(tf)) * 100)
            except Exception:
                pct = None

        if pct is not None:
            try:
                pct_i = int(pct)
            except Exception:
                pct_i = 0
            if pct_i < 0:
                pct_i = 0
            if pct_i > 100:
                pct_i = 100
            _PROGRESS["percent"] = pct_i


def add_progress_detail(key: str, amount: int = 1) -> None:
    """Increment a numeric detail counter (safe if missing)."""
    if not key:
        return
    try:
        delta = int(amount)
    except Exception:
        delta = 0
    with _LOCK:
        _PROGRESS.setdefault("details", {})
        cur = _PROGRESS["details"].get(key, 0)
        try:
            cur_i = int(cur)
        except Exception:
            cur_i = 0
        _PROGRESS["details"][key] = cur_i + delta


def get_progress() -> dict:
    """Return a UI-friendly snapshot of progress + recent logs."""
    with _LOCK:
        return {
            "status": _PROGRESS.get("status", "IDLE"),
            "percent": int(_PROGRESS.get("percent", 0) or 0),
            "current_step": _PROGRESS.get("current_step", ""),
            "details": dict(_PROGRESS.get("details", {}) or {}),
            "log": list(_LOGS),
        }
