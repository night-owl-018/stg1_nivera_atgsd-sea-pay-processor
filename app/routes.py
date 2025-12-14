# app/routes.py

import os
import json
import threading
import time
import inspect
from pathlib import Path
from flask import Blueprint, jsonify, request, send_from_directory, Response, abort

bp = Blueprint("routes", __name__)

# ---- Paths (match your container layout) ----
DATA_DIR = Path("/app/data")
OUTPUT_DIR = Path("/app/output")
REVIEW_JSON_PATH = OUTPUT_DIR / "SEA_PAY_REVIEW.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- Log + Progress plumbing ----
# Prefer your existing app.core.logger if present (so we don't break anything),
# but provide a safe fallback if it doesn't exist.
try:
    from app.core.logger import log as _log  # type: ignore
    from app.core.logger import clear_logs as _clear_logs  # type: ignore
    from app.core.logger import get_logs as _get_logs  # type: ignore
except Exception:
    _LOGS = []
    _LOG_LOCK = threading.Lock()

    def _log(msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        with _LOG_LOCK:
            _LOGS.append(line)
            if len(_LOGS) > 5000:
                _LOGS[:] = _LOGS[-4000:]

    def _clear_logs():
        with _LOG_LOCK:
            _LOGS.clear()

    def _get_logs(limit: int = 500):
        with _LOG_LOCK:
            return _LOGS[-limit:]


_PROGRESS_LOCK = threading.Lock()
_PROGRESS = {
    "status": "idle",      # idle | processing | complete | error
    "percent": 0,          # 0..100
    "message": "",
    "started_at": None,
    "finished_at": None,
    "total_files": 0,
    "done_files": 0,
    "error": None,
}


def _set_progress(**kwargs):
    with _PROGRESS_LOCK:
        for k, v in kwargs.items():
            _PROGRESS[k] = v


def _get_progress():
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


# We compute percent reliably even if processing.py doesn't emit explicit percent:
# - total_files set when upload starts
# - done_files inferred from logs (OCR → ...) OR fallback to simple step logic
def _recompute_percent_from_logs():
    prog = _get_progress()
    if prog["status"] not in ("processing", "complete"):
        return prog

    logs = _get_logs(limit=2000)
    total = prog.get("total_files") or 0

    # "OCR → filename" appears once per input pdf in your logs.
    ocr_count = sum(1 for line in logs if "OCR →" in line)

    # If you had multiple "=== PROCESS STARTED ===" lines, still fine; count OCR lines.
    done_files = max(prog.get("done_files") or 0, ocr_count)

    percent = prog.get("percent") or 0
    if total > 0:
        # Weight: OCR stage is the real "file progress"
        percent = int(min(99, (done_files / total) * 100))
    else:
        # Unknown total, keep whatever we have
        percent = percent or 0

    # If complete appears in log, force 100
    if any("PROCESS COMPLETE" in line for line in logs):
        percent = 100

    prog.update({"done_files": done_files, "percent": percent})
    _set_progress(done_files=done_files, percent=percent)
    return prog


# ---- Processing runner (call your existing processing function safely) ----
def _run_processing():
    """
    Runs processing using your existing app.processing module.
    This function tries multiple possible entrypoints so we don't break your setup.
    """
    try:
        _log("=== PROCESS STARTED ===")

        # Import here so your app boot still works even if processing has heavy imports.
        import app.processing as processing  # type: ignore

        # Try to find a callable entry point without guessing too hard.
        candidates = [
            "process_all",
            "process_documents",
            "process_files",
            "run",
            "run_processing",
            "main",
        ]

        fn = None
        for name in candidates:
            if hasattr(processing, name) and callable(getattr(processing, name)):
                fn = getattr(processing, name)
                break

        if fn is None:
            raise RuntimeError(
                "No processing entrypoint found in app.processing. "
                "Tried: " + ", ".join(candidates)
            )

        sig = inspect.signature(fn)
        kwargs = {}

        # Only pass args the function actually accepts.
        if "data_dir" in sig.parameters:
            kwargs["data_dir"] = str(DATA_DIR)
        if "input_dir" in sig.parameters:
            kwargs["input_dir"] = str(DATA_DIR)
        if "output_dir" in sig.parameters:
            kwargs["output_dir"] = str(OUTPUT_DIR)
        if "out_dir" in sig.parameters:
            kwargs["out_dir"] = str(OUTPUT_DIR)

        # Some versions accept logger/progress callbacks
        if "log_fn" in sig.parameters:
            kwargs["log_fn"] = _log
        if "logger" in sig.parameters:
            kwargs["logger"] = _log
        if "progress_fn" in sig.parameters:
            kwargs["progress_fn"] = lambda p, m="": _set_progress(percent=int(p), message=m)

        # Run
        fn(**kwargs)

        _set_progress(status="complete", percent=100, message="Complete", finished_at=time.time())
        _log("PROCESS COMPLETE")

    except Exception as e:
        _set_progress(status="error", error=str(e), message="Error")
        _log(f"[ERROR] {e}")


# ---- Routes ----

@bp.route("/", methods=["GET"])
def index():
    # Your frontend lives at /app/web/frontend/index.html in your structure
    # but in the container it is mounted as /app/web/frontend.
    # We serve it directly from disk to avoid template issues.
    frontend_dir = Path("/app/web/frontend")
    return send_from_directory(frontend_dir, "index.html")


@bp.route("/web/frontend/<path:filename>", methods=["GET"])
def frontend_static(filename):
    frontend_dir = Path("/app/web/frontend")
    return send_from_directory(frontend_dir, filename)


@bp.route("/logs/clear", methods=["POST"])
def clear_logs():
    _clear_logs()
    # keep progress intact
    return jsonify({"ok": True})


@bp.route("/logs", methods=["GET"])
def logs():
    limit = int(request.args.get("limit", "500"))
    return jsonify({"lines": _get_logs(limit=limit)})


@bp.route("/progress", methods=["GET"])
def progress():
    prog = _recompute_percent_from_logs()
    # Include last log lines so UI can update "LIVE LOG" without extra calls
    lines = _get_logs(limit=300)
    return jsonify({
        "status": prog["status"],
        "percent": prog["percent"],
        "message": prog.get("message", ""),
        "total_files": prog.get("total_files", 0),
        "done_files": prog.get("done_files", 0),
        "error": prog.get("error"),
        "lines": lines,
    })


@bp.route("/process", methods=["POST"])
def process():
    # Reset state but do not touch anything else.
    _clear_logs()
    _set_progress(
        status="processing",
        percent=0,
        message="Processing",
        started_at=time.time(),
        finished_at=None,
        done_files=0,
        error=None,
    )

    files = request.files.getlist("files")
    if not files:
        abort(400, "No files uploaded")

    # Save uploaded PDFs
    saved = 0
    for f in files:
        if not f.filename:
            continue
        out_path = DATA_DIR / f.filename
        f.save(out_path)
        _log(f"SAVED INPUT FILE → {out_path}")
        saved += 1

    _set_progress(total_files=saved)

    # Start worker thread
    t = threading.Thread(target=_run_processing, daemon=True)
    t.start()

    return jsonify({"ok": True, "saved": saved})


# ---- Review & Override API ----

def _safe_load_review():
    if not REVIEW_JSON_PATH.exists():
        return None
    try:
        return json.loads(REVIEW_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_members(review_obj):
    """
    Supports multiple formats:
    - {"members": [{"member": "...", "sheets": [...]}, ...]}
    - {"STG1 LAST,FIRST": {...}}
    - {"members": ["A", "B", ...]}
    """
    if not review_obj:
        return []

    if isinstance(review_obj, dict) and "members" in review_obj:
        mem = review_obj["members"]
        if isinstance(mem, list):
            if mem and isinstance(mem[0], dict):
                out = []
                for m in mem:
                    name = m.get("member") or m.get("name") or m.get("id")
                    if name:
                        out.append(name)
                return sorted(set(out))
            if mem and isinstance(mem[0], str):
                return sorted(set(mem))

    # dict keyed by member name
    if isinstance(review_obj, dict):
        # filter out known non-member keys
        skip = {"meta", "version", "generated_at", "members"}
        keys = [k for k in review_obj.keys() if k not in skip and isinstance(k, str)]
        return sorted(keys)

    return []


def _extract_sheets_for_member(review_obj, member_name: str):
    if not review_obj:
        return []

    # Format 1: members list of dicts
    if isinstance(review_obj, dict) and "members" in review_obj and isinstance(review_obj["members"], list):
        for m in review_obj["members"]:
            if not isinstance(m, dict):
                continue
            name = m.get("member") or m.get("name") or m.get("id")
            if name == member_name:
                return m.get("sheets") or m.get("data") or m.get("items") or []

    # Format 2: dict keyed by member
    if isinstance(review_obj, dict) and member_name in review_obj:
        mobj = review_obj[member_name]
        if isinstance(mobj, dict):
            return mobj.get("sheets") or mobj.get("data") or mobj.get("items") or mobj.get("rows") or []
        if isinstance(mobj, list):
            return mobj

    return []


@bp.route("/api/members", methods=["GET"])
def api_members():
    review = _safe_load_review()
    members = _extract_members(review)
    return jsonify({"members": members})


@bp.route("/api/sheets", methods=["GET"])
def api_sheets():
    member = request.args.get("member", "").strip()
    if not member:
        return jsonify({"sheets": []})

    review = _safe_load_review()
    sheets = _extract_sheets_for_member(review, member)

    return jsonify({"member": member, "sheets": sheets})


@bp.route("/api/sheets/save", methods=["POST"])
def api_sheets_save():
    payload = request.get_json(silent=True) or {}
    member = (payload.get("member") or "").strip()
    sheets = payload.get("sheets")

    if not member or sheets is None:
        abort(400, "Missing member or sheets")

    review = _safe_load_review()
    if review is None:
        review = {}

    # Save back in the safest way depending on structure.
    if isinstance(review, dict) and "members" in review and isinstance(review["members"], list):
        updated = False
        for m in review["members"]:
            if isinstance(m, dict):
                name = m.get("member") or m.get("name") or m.get("id")
                if name == member:
                    m["sheets"] = sheets
                    updated = True
                    break
        if not updated:
            review["members"].append({"member": member, "sheets": sheets})
    elif isinstance(review, dict):
        if member not in review or not isinstance(review[member], dict):
            review[member] = {}
        if isinstance(review[member], dict):
            review[member]["sheets"] = sheets
        else:
            review[member] = {"sheets": sheets}
    else:
        # If it's some weird format, wrap it.
        review = {"members": [{"member": member, "sheets": sheets}]}

    REVIEW_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_JSON_PATH.write_text(json.dumps(review, indent=2), encoding="utf-8")

    return jsonify({"ok": True})
