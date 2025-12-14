import threading
import time

# =========================================================
# GLOBAL STATE (legacy-safe)
# =========================================================

_LOG_LOCK = threading.Lock()

LIVE_LOGS = []

PROGRESS = {
    "status": "IDLE",
    "percent": 0,
    "logs": LIVE_LOGS,
}

# =========================================================
# LEGACY FUNCTION (DO NOT CHANGE)
# =========================================================

def add_progress_detail(message):
    # This function MUST exist — processing.py depends on it
    log(message)

# =========================================================
# CORE LOGGING
# =========================================================

def _timestamp():
    return time.strftime("[%H:%M:%S]")

def log(message):
    if message is None:
        return

    msg = str(message)
    if not msg.startswith("["):
        msg = f"{_timestamp()} {msg}"

    with _LOG_LOCK:
        LIVE_LOGS.append(msg)
        if len(LIVE_LOGS) > 5000:
            del LIVE_LOGS[:1000]

def clear_logs():
    with _LOG_LOCK:
        LIVE_LOGS.clear()

def get_logs():
    with _LOG_LOCK:
        return list(LIVE_LOGS)

# =========================================================
# PROGRESS (UI SAFE)
# =========================================================

def reset_progress():
    with _LOG_LOCK:
        PROGRESS["status"] = "IDLE"
        PROGRESS["percent"] = 0

def set_progress(status=None, percent=None):
    with _LOG_LOCK:
        if status is not None:
            PROGRESS["status"] = status
        if percent is not None:
            try:
                PROGRESS["percent"] = max(0, min(100, int(percent)))
            except Exception:
                pass

def get_progress():
    with _LOG_LOCK:
        return {
            "status": PROGRESS.get("status", "IDLE"),
            "percent": PROGRESS.get("percent", 0),
            "log": list(LIVE_LOGS),
        }
