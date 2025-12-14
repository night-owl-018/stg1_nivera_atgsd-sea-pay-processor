import os
import time
import threading

LOG_FILE = os.path.join("/app/output", "app.log")

PROGRESS = {
    "status": "IDLE",
    "percentage": 0,
    "logs": [],
}

PROGRESS_LOCK = threading.Lock()


# =========================================================
# Legacy helper (kept as-is)
# =========================================================
...
def add_progress_detail(message):
    with PROGRESS_LOCK:
        PROGRESS["logs"].append(message)


# =========================================================
# PATCH: UI log + progress helpers (minimal, backwards-safe)
# =========================================================

# Keep an in-memory rolling log for the UI (and for /progress).
_LOG_LINES = []
_LOG_LOCK = threading.Lock()
_MAX_LOG_LINES = 2000


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _ensure_progress_keys():
    # Normalize keys used across older/newer code paths.
    with PROGRESS_LOCK:
        if "logs" not in PROGRESS:
            PROGRESS["logs"] = []
        if "log" not in PROGRESS:
            PROGRESS["log"] = PROGRESS["logs"]
        if "percentage" in PROGRESS and "percent" not in PROGRESS:
            PROGRESS["percent"] = PROGRESS.get("percentage", 0)
        if "percent" in PROGRESS and "percentage" not in PROGRESS:
            PROGRESS["percentage"] = PROGRESS.get("percent", 0)
        if "status" not in PROGRESS:
            PROGRESS["status"] = "IDLE"


def log(message: str):
    # Add a log line for Live Log + /progress polling (and also app.log best-effort).
    line = str(message)
    if not line.startswith("["):
        line = f"[{_timestamp()}] {line}"

    with _LOG_LOCK:
        _LOG_LINES.append(line)
        if len(_LOG_LINES) > _MAX_LOG_LINES:
            del _LOG_LINES[: len(_LOG_LINES) - _MAX_LOG_LINES]

    _ensure_progress_keys()
    with PROGRESS_LOCK:
        PROGRESS["logs"].append(line)
        PROGRESS["log"] = PROGRESS["logs"]

        if len(PROGRESS["logs"]) > _MAX_LOG_LINES:
            del PROGRESS["logs"][: len(PROGRESS["logs"]) - _MAX_LOG_LINES]
            PROGRESS["log"] = PROGRESS["logs"]

    # Best-effort file write (never crash the app if disk is read-only, etc.)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def clear_logs():
    # Clear in-memory logs and truncate on-disk log file.
    with _LOG_LOCK:
        _LOG_LINES.clear()

    _ensure_progress_keys()
    with PROGRESS_LOCK:
        PROGRESS["logs"].clear()
        PROGRESS["log"] = PROGRESS["logs"]

    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass


def get_logs():
    # Return rolling in-memory log as a list of lines.
    with _LOG_LOCK:
        return list(_LOG_LINES)


def set_progress(status=None, percent=None, percentage=None):
    # Update progress in a way that's compatible with both "percent" and "percentage".
    _ensure_progress_keys()
    with PROGRESS_LOCK:
        if status is not None:
            PROGRESS["status"] = status

        if percent is None and percentage is not None:
            percent = percentage

        if percent is not None:
            try:
                p = int(percent)
            except Exception:
                p = 0
            p = max(0, min(100, p))
            PROGRESS["percent"] = p
            PROGRESS["percentage"] = p


def reset_progress():
    # Reset progress (does not wipe logs unless clear_logs is also called).
    _ensure_progress_keys()
    with PROGRESS_LOCK:
        PROGRESS["status"] = "IDLE"
        PROGRESS["percent"] = 0
        PROGRESS["percentage"] = 0


def get_progress():
    # Progress payload used by /progress.
    _ensure_progress_keys()
    with PROGRESS_LOCK:
        return {
            "status": PROGRESS.get("status", "IDLE"),
            "percent": int(PROGRESS.get("percent", PROGRESS.get("percentage", 0)) or 0),
            "log": list(PROGRESS.get("logs", [])),
        }
