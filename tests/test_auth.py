import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import auth


class AuthTests(unittest.TestCase):
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

    def test_register_and_authenticate_user(self):
        username = auth.register_user(" Alice_01 ", "secret1")

        self.assertEqual(username, "alice_01")
        self.assertEqual(auth.authenticate_user("ALICE_01", "secret1"), "alice_01")
        self.assertIsNone(auth.authenticate_user("alice_01", "wrong-password"))

    def test_duplicate_username_is_rejected_case_insensitively(self):
        auth.register_user("alice", "secret1")

        with self.assertRaises(auth.AuthValidationError):
            auth.register_user("ALICE", "secret2")

    def test_invalid_username_is_rejected(self):
        for username in ("ab", "user-name", "中文账号"):
            with self.subTest(username=username):
                with self.assertRaises(auth.AuthValidationError):
                    auth.register_user(username, "secret1")

    def test_invalid_password_length_is_rejected(self):
        for password in ("12345", "x" * 129):
            with self.subTest(length=len(password)):
                with self.assertRaises(auth.AuthValidationError):
                    auth.register_user("alice", password)

    def test_concurrent_registration_keeps_all_users(self):
        usernames = [f"user_{index}" for index in range(12)]
        with ThreadPoolExecutor(max_workers=6) as executor:
            registered = list(executor.map(lambda username: auth.register_user(username, "secret1"), usernames))

        payload = json.loads(self.users_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(registered), sorted(usernames))
        self.assertEqual(
            sorted(item["username"] for item in payload["users"]),
            sorted(usernames),
        )


if __name__ == "__main__":
    unittest.main()
