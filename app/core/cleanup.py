import os
import shutil

from app.core.config import DATA_DIR, OUTPUT_DIR
from app.core.logger import log


# ------------------------------------------------
# CLEANUP FUNCTIONS
# ------------------------------------------------

def cleanup_folder(folder_path, folder_name):
    try:
        files_deleted = 0
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                files_deleted += 1

        if files_deleted > 0:
            log(f"🗑 CLEANED {folder_name}: {files_deleted} files deleted")
        return files_deleted
    except Exception as e:
        log(f"❌ CLEANUP ERROR in {folder_name}: {e}")
        return 0


def cleanup_all_folders():
    log("=== STARTING RESET/CLEANUP ===")
    total = 0
    total += cleanup_folder(DATA_DIR, "INPUT/DATA")
    total += cleanup_folder(OUTPUT_DIR, "OUTPUT")

    # Also clear marked sheets and summary
    marked_dir = os.path.join(OUTPUT_DIR, "marked_sheets")
    summary_dir = os.path.join(OUTPUT_DIR, "summary")
    if os.path.exists(marked_dir):
        total += cleanup_folder(marked_dir, "MARKED_SHEETS")
    if os.path.exists(summary_dir):
        total += cleanup_folder(summary_dir, "SUMMARY")

    log(f"✅ RESET COMPLETE: {total} total files deleted")
    log("🗑 CLEARING ALL LOGS...")
    log("=" * 50)
    return total
