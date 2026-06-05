from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db
from auth import USERS_PATH
from storage import RESULT_DIR


def _load_users() -> list[dict[str, object]]:
    if not USERS_PATH.exists():
        return []
    payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    users = payload.get("users")
    return [item for item in users if isinstance(item, dict)] if isinstance(users, list) else []


def _load_results() -> list[dict[str, object]]:
    results = []
    for path in sorted(RESULT_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"skip invalid result JSON: {path.name}")
            continue
        if isinstance(payload, dict):
            payload.setdefault("task_id", path.stem)
            results.append(payload)
    return results


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def migrate(*, dry_run: bool = False) -> dict[str, int]:
    users = _load_users()
    results = _load_results()
    if dry_run:
        return {"users": len(users), "results": len(results)}

    db.initialize_database()
    for user in users:
        username = str(user.get("username") or "").strip().lower()
        password = str(user.get("password") or "")
        if not username or not password:
            continue
        db.execute(
            """
            INSERT INTO users (username, password, created_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                password = VALUES(password),
                created_at = COALESCE(users.created_at, VALUES(created_at))
            """,
            (username, password, _parse_datetime(user.get("created_at"))),
        )

    for result in results:
        task_id = str(result.get("task_id") or "").strip()
        if not task_id:
            continue
        owner_username = str(result.get("owner_username") or "")
        status = str(result.get("status") or "unknown")
        db.execute(
            """
            INSERT INTO results (task_id, owner_username, status, saved_at, finished_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                owner_username = VALUES(owner_username),
                status = VALUES(status),
                saved_at = VALUES(saved_at),
                finished_at = VALUES(finished_at),
                payload = VALUES(payload)
            """,
            (
                task_id,
                owner_username,
                status,
                _parse_datetime(result.get("saved_at")),
                _parse_datetime(result.get("finished_at")),
                json.dumps(result, ensure_ascii=False),
            ),
        )

    return {"users": len(users), "results": len(results)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate local JSON users and results to MySQL.")
    parser.add_argument("--dry-run", action="store_true", help="Only count local records.")
    args = parser.parse_args()
    counts = migrate(dry_run=args.dry_run)
    print(f"users={counts['users']} results={counts['results']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
