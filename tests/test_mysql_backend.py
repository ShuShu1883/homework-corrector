import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import auth
import db
import storage
import scripts.migrate_json_to_mysql as migration


FIXED_UTC_NOW = datetime(2026, 6, 5, 2, 20, 17, tzinfo=timezone.utc)


class MySQLAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.users_path = self.data_dir / "users.json"
        self.patches = [
            patch.object(auth, "USERS_PATH", self.users_path),
            patch.object(auth, "ensure_runtime_dirs", lambda: self.data_dir.mkdir(parents=True, exist_ok=True)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_register_user_uses_mysql_when_enabled(self):
        with (
            patch.object(auth, "is_mysql_enabled", return_value=True),
            patch.object(auth, "initialize_database") as initialize_database,
            patch.object(auth, "fetch_one", return_value=None),
            patch.object(auth, "execute") as execute,
            patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
        ):
            username = auth.register_user("Alice_01", "secret1")

        self.assertEqual(username, "alice_01")
        initialize_database.assert_called_once()
        self.assertIn("INSERT INTO users", execute.call_args.args[0])
        self.assertEqual(execute.call_args.args[1][0:2], ("alice_01", "secret1"))
        self.assertEqual(execute.call_args.args[1][2], datetime(2026, 6, 5, 10, 20, 17))
        self.assertFalse(self.users_path.exists())

    def test_authenticate_user_uses_mysql_when_enabled(self):
        with (
            patch.object(auth, "is_mysql_enabled", return_value=True),
            patch.object(auth, "initialize_database"),
            patch.object(auth, "fetch_one", return_value={"username": "alice", "password": "secret1"}),
        ):
            self.assertEqual(auth.authenticate_user("ALICE", "secret1"), "alice")
            self.assertIsNone(auth.authenticate_user("ALICE", "wrong"))

    def test_mysql_auth_failure_falls_back_to_json(self):
        self.data_dir.mkdir(parents=True)
        self.users_path.write_text(
            json.dumps({"users": [{"username": "alice", "password": "secret1"}]}),
            encoding="utf-8",
        )

        with (
            patch.object(auth, "is_mysql_enabled", return_value=True),
            patch.object(auth, "initialize_database", side_effect=db.DatabaseError("offline")),
        ):
            self.assertEqual(auth.authenticate_user("alice", "secret1"), "alice")

    def test_mysql_duplicate_key_is_not_treated_as_fallback(self):
        with (
            patch.object(auth, "is_mysql_enabled", return_value=True),
            patch.object(auth, "initialize_database"),
            patch.object(auth, "fetch_one", return_value=None),
            patch.object(auth, "execute", side_effect=db.DatabaseError("Duplicate entry 'alice' for key 'PRIMARY'")),
        ):
            with self.assertRaises(auth.AuthValidationError):
                auth.register_user("alice", "secret1")

        self.assertFalse(self.users_path.exists())


class MySQLStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.result_dir = Path(self.temp_dir.name) / "results"
        self.result_dir.mkdir()
        self.patches = [
            patch.object(storage, "RESULT_DIR", self.result_dir),
            patch.object(storage, "ensure_runtime_dirs", lambda: self.result_dir.mkdir(parents=True, exist_ok=True)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_save_result_uses_mysql_when_enabled(self):
        with (
            patch.object(storage, "is_mysql_enabled", return_value=True),
            patch.object(storage, "initialize_database") as initialize_database,
            patch.object(storage, "execute") as execute,
            patch("time_utils._utc_now", return_value=FIXED_UTC_NOW),
        ):
            storage.save_result(
                "task-1",
                {"owner_username": "alice", "status": "finished", "finished_at": "2026-06-05T10:30:00"},
            )

        initialize_database.assert_called_once()
        self.assertIn("INSERT INTO results", execute.call_args.args[0])
        args = execute.call_args.args[1]
        self.assertEqual(args[0:3], ("task-1", "alice", "finished"))
        self.assertEqual(args[3], datetime(2026, 6, 5, 10, 20, 17))
        self.assertEqual(json.loads(args[5])["task_id"], "task-1")
        self.assertEqual(json.loads(args[5])["saved_at"], "2026-06-05T10:20:17")
        self.assertFalse((self.result_dir / "task-1.json").exists())

    def test_load_and_list_results_use_mysql_owner_filter(self):
        payload = {"task_id": "task-1", "owner_username": "alice", "status": "finished"}
        with (
            patch.object(storage, "is_mysql_enabled", return_value=True),
            patch.object(storage, "initialize_database"),
            patch.object(storage, "fetch_one", return_value={"payload": json.dumps(payload)}) as fetch_one,
            patch.object(storage, "fetch_all", return_value=[{"payload": json.dumps(payload)}]),
        ):
            self.assertEqual(storage.load_result("task-1", owner_username="alice"), payload)
            self.assertEqual(storage.list_results(owner_username="alice"), [payload])

        self.assertIn("owner_username = %s", fetch_one.call_args.args[0])
        self.assertEqual(fetch_one.call_args.args[1], ("task-1", "alice"))

    def test_mysql_storage_failure_falls_back_to_json(self):
        storage._save_result_json("task-1", {"owner_username": "alice", "status": "finished"})

        with (
            patch.object(storage, "is_mysql_enabled", return_value=True),
            patch.object(storage, "initialize_database", side_effect=db.DatabaseError("offline")),
        ):
            loaded = storage.load_result("task-1", owner_username="alice")

        self.assertEqual(loaded["task_id"], "task-1")
        self.assertEqual(loaded["owner_username"], "alice")


class MigrationScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.users_path = self.root / "data" / "users.json"
        self.result_dir = self.root / "results"
        self.users_path.parent.mkdir()
        self.result_dir.mkdir()
        self.users_path.write_text(
            json.dumps({"users": [{"username": "alice", "password": "secret1", "created_at": "2026-06-05T10:20:17"}]}),
            encoding="utf-8",
        )
        (self.result_dir / "task-1.json").write_text(
            json.dumps(
                {
                    "task_id": "task-1",
                    "owner_username": "alice",
                    "status": "finished",
                    "saved_at": "2026-06-05T10:30:00",
                }
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(migration, "USERS_PATH", self.users_path),
            patch.object(migration, "RESULT_DIR", self.result_dir),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_dry_run_counts_local_json_records(self):
        self.assertEqual(migration.migrate(dry_run=True), {"users": 1, "results": 1})

    def test_migration_uses_upserts(self):
        with (
            patch.object(migration.db, "initialize_database") as initialize_database,
            patch.object(migration.db, "execute") as execute,
        ):
            counts = migration.migrate()

        self.assertEqual(counts, {"users": 1, "results": 1})
        initialize_database.assert_called_once()
        self.assertEqual(execute.call_count, 2)
        self.assertTrue(all("ON DUPLICATE KEY UPDATE" in call.args[0] for call in execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
