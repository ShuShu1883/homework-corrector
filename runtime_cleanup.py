from __future__ import annotations

import time
from pathlib import Path

from config import CUT_DIR, DEBUG_DIR, PROCESSED_DIR, UPLOAD_DIR, ensure_runtime_dirs, get_int_setting


TRANSIENT_DIRS = (UPLOAD_DIR, PROCESSED_DIR, CUT_DIR, DEBUG_DIR)
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_MAX_FILES_PER_DIR = 120
_last_cleanup_at = 0.0


def _runtime_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in directory.iterdir() if path.is_file() and path.name != ".gitkeep"]


def cleanup_runtime_files(*, force: bool = False) -> dict[str, int]:
    global _last_cleanup_at

    ensure_runtime_dirs()
    now = time.time()
    interval_seconds = max(60, get_int_setting("CLEANUP_INTERVAL_SECONDS", 600))
    if not force and now - _last_cleanup_at < interval_seconds:
        return {"deleted": 0, "scanned": 0}

    _last_cleanup_at = now
    max_age_seconds = max(1, get_int_setting("CLEANUP_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS)) * 3600
    max_files_per_dir = max(1, get_int_setting("CLEANUP_MAX_FILES_PER_DIR", DEFAULT_MAX_FILES_PER_DIR))

    deleted = 0
    scanned = 0
    for directory in TRANSIENT_DIRS:
        files = _runtime_files(directory)
        scanned += len(files)

        for path in files:
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    deleted += 1
            except OSError:
                pass

        remaining = sorted(_runtime_files(directory), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in remaining[max_files_per_dir:]:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass

    return {"deleted": deleted, "scanned": scanned}


def clear_runtime_files() -> dict[str, int]:
    ensure_runtime_dirs()
    deleted = 0
    scanned = 0
    for directory in TRANSIENT_DIRS:
        for path in _runtime_files(directory):
            scanned += 1
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
    return {"deleted": deleted, "scanned": scanned}
