import csv
import os
import re
from difflib import SequenceMatcher

from app.core.config import RATE_FILE
from app.core.logger import log
from app.core.ships import normalize


# ------------------------------------------------
# LOAD RATES
# ------------------------------------------------

def _clean_header(h):
    return h.lstrip("\ufeff").strip().strip('"').lower() if h else ""


def load_rates():
    rates = {}
    if not os.path.exists(RATE_FILE):
        log("RATE FILE MISSING")
        return rates

    with open(RATE_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        reader.fieldnames = [_clean_header(h) for h in reader.fieldnames]

        for row in reader:
            last = (row.get("last") or "").upper().strip()
            first = (row.get("first") or "").upper().strip()
            rate = (row.get("rate") or "").upper().strip()
            if last and rate:
                rates[f"{last},{first}"] = rate

    log(f"RATES LOADED: {len(rates)}")
    return rates


RATES = load_rates()

CSV_IDENTITIES = []
for key, rate in RATES.items():
    last, first = key.split(",", 1)

    def normalize_for_id(text):
        t = re.sub(r"\(.*?\)", "", text.upper())
        t = re.sub(r"[^A-Z ]", "", t)
        return " ".join(t.split())

    full_norm = normalize_for_id(f"{first} {last}")
    CSV_IDENTITIES.append((full_norm, rate, last, first))


# ------------------------------------------------
# CSV MATCHING / IDENTITY
# ------------------------------------------------

def lookup_csv_identity(name):
    ocr_norm = normalize(name)
    best = None
    best_score = 0.0

    for csv_norm, rate, last, first in CSV_IDENTITIES:
        score = SequenceMatcher(None, ocr_norm, csv_norm).ratio()
        if score > best_score:
            best_score = score
            best = (rate, last, first)

    if best and best_score >= 0.60:
        rate, last, first = best
        log(f"CSV MATCH ({best_score:.2f}) → {rate} {last},{first}")
        return best

    log(f"CSV NO GOOD MATCH (best={best_score:.2f}) for [{name}]")
    return None


def get_rate(name):
    parts = normalize(name).split()
    if len(parts) < 2:
        return ""
    key = f"{parts[-1]},{parts[0]}"
    return RATES.get(key, "")


def resolve_identity(name):
    csv_id = lookup_csv_identity(name)
    if csv_id:
        rate, last, first = csv_id
    else:
        parts = name.split()
        last = parts[-1]
        first = " ".join(parts[:-1])
        rate = get_rate(name)
    return rate, last, first
