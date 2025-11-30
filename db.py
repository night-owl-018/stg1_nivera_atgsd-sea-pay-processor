import os
import sqlite3
import csv
from datetime import datetime
from pathlib import Path
import re

# ---------------------------
# DATABASE LOCATION
# ---------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.environ.get("SEA_PAY_DB", os.path.join(ROOT, "seapay.db"))

SHIP_FILE = os.path.join(ROOT, "ships.txt")
RATE_FILE = os.path.join(ROOT, "atgsd_n811.csv")


# ---------------------------
# CONNECTION
# ---------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------------------------
# SCHEMA
# ---------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS ships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    normalized TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS names (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    last TEXT NOT NULL,
    first TEXT NOT NULL,
    rate TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(last, first)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    input_path TEXT,
    output_path TEXT,
    template TEXT,
    status TEXT,
    notes TEXT,
    exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    run_id INTEGER
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    version_label TEXT,
    upload_time TEXT NOT NULL,
    last_used TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
"""


# ---------------------------
# INIT DB
# ---------------------------

def init_db():
    Path(os.path.dirname(DB_PATH) or ".").mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ---------------------------
# NORMALIZATION (MATCHES YOUR EXISTING CODE)
# ---------------------------

def normalize(text: str):
    text = re.sub(r"\(.*?\)", "", text or "")
    text = re.sub(r"[^A-Z ]", "", text.upper())
    return " ".join(text.split())


# ---------------------------
# CSV HEADER CLEANER
# ---------------------------

def clean_header(h):
    return (h or "").lstrip("\ufeff").strip().strip('"').lower()


# ---------------------------
# SEED SHIPS
# ---------------------------

def seed_ships():
    if not os.path.exists(SHIP_FILE):
        print("ships.txt not found, skipping")
        return

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM ships")
    if cur.fetchone()[0] > 0:
        print("Ships already in DB, skipping")
        conn.close()
        return

    now = now_iso()
    rows = []

    with open(SHIP_FILE, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip()
            if not name:
                continue
            rows.append((name.upper(), normalize(name), 1, now))

    cur.executemany(
        "INSERT OR IGNORE INTO ships (name, normalized, active, created_at) VALUES (?, ?, ?, ?)",
        rows
    )
    conn.commit()
    conn.close()

    print(f"Inserted {len(rows)} ships")


# ---------------------------
# SEED NAMES
# ---------------------------

def seed_names():
    if not os.path.exists(RATE_FILE):
        print("atgsd_n811.csv not found, skipping")
        return

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM names")
    if cur.fetchone()[0] > 0:
        print("Names already in DB, skipping")
        conn.close()
        return

    now = now_iso()
    rows = []

    with open(RATE_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [clean_header(h) for h in reader.fieldnames]

        for row in reader:
            last = (row.get("last") or "").upper().strip()
            first = (row.get("first") or "").upper().strip()
            rate = (row.get("rate") or "").upper().strip()

            if last and rate:
                rows.append((last, first, rate, 1, now))

    cur.executemany(
        "INSERT OR IGNORE INTO names (last, first, rate, active, created_at) VALUES (?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    conn.close()

    print(f"Inserted {len(rows)} names")


# ---------------------------
# RUN EVERYTHING
# ---------------------------

def initialize_and_seed():
    print(f"Using DB: {DB_PATH}")
    init_db()
    seed_ships()
    seed_names()
    print("DB READY")


if __name__ == "__main__":
    initialize_and_seed()
