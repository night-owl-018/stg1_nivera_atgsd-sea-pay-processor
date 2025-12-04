from collections import deque
from datetime import datetime

# ------------------------------------------------
# LIVE LOG BUFFER
# ------------------------------------------------

LIVE_LOGS = deque(maxlen=500)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LIVE_LOGS.append(line)


def clear_logs():
    LIVE_LOGS.clear()
    print("Logs cleared", flush=True)
