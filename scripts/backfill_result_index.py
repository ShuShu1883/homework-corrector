from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from homework_corrector.storage.cos_storage import is_cos_enabled, list_keys, load_json, object_key
from homework_corrector.storage.db import initialize_database, is_mysql_enabled
from homework_corrector.storage.storage import save_result_index


def _parse_key_defaults(key: str) -> dict[str, str]:
    parts = key.strip("/").split("/")
    defaults: dict[str, str] = {"cos_result_key": key, "cos_prefix": key.rsplit("/result.json", 1)[0]}
    try:
        users_index = parts.index("users")
        defaults["owner_username"] = parts[users_index + 1]
        defaults["task_id"] = parts[users_index + 3]
    except (ValueError, IndexError):
        pass
    return defaults


def _result_keys(owner_username: str | None) -> list[str]:
    if owner_username:
        prefix = object_key("users", owner_username.strip().lower(), "tasks")
    else:
        prefix = object_key("users")
    return [key for key in list_keys(prefix) if key.endswith("/result.json")]


def _prepare_payload(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    defaults = _parse_key_defaults(key)
    prepared = {**defaults, **payload}
    prepared.setdefault("storage_backend", "cos")
    prepared.setdefault("cos_result_key", key)
    prepared.setdefault("cos_prefix", defaults.get("cos_prefix"))
    return prepared


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill MySQL result index from COS result.json files.")
    parser.add_argument("--owner", help="Only backfill one normalized username.")
    parser.add_argument("--limit", type=int, help="Maximum number of COS result files to inspect.")
    parser.add_argument("--apply", action="store_true", help="Write indexes to MySQL. Defaults to dry-run.")
    args = parser.parse_args()

    if not is_cos_enabled():
        raise SystemExit("FILE_STORAGE_BACKEND is not cos; nothing to backfill.")
    if not is_mysql_enabled():
        raise SystemExit("DB_BACKEND is not mysql; result index cannot be written.")

    initialize_database()
    keys = _result_keys(args.owner)
    if args.limit is not None:
        keys = keys[: max(0, args.limit)]

    mode = "apply" if args.apply else "dry-run"
    print(f"mode={mode} result_files={len(keys)}")

    written = 0
    skipped = 0
    for key in keys:
        payload = load_json(key)
        if not payload:
            skipped += 1
            print(f"skip unreadable {key}")
            continue

        prepared = _prepare_payload(key, payload)
        summary = {
            "task_id": prepared.get("task_id"),
            "owner_username": prepared.get("owner_username"),
            "status": prepared.get("status"),
            "subject": prepared.get("subject"),
            "cos_result_key": prepared.get("cos_result_key"),
        }
        if args.apply:
            save_result_index(prepared)
            written += 1
            print(f"indexed {summary}")
        else:
            print(f"would_index {summary}")

    print(f"done written={written} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
