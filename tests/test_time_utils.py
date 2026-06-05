import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import time_utils


class BeijingTimeTests(unittest.TestCase):
    def test_beijing_now_converts_from_utc_without_timezone_suffix(self):
        utc_now = datetime(2026, 6, 5, 2, 30, 45, tzinfo=timezone.utc)

        with patch.object(time_utils, "_utc_now", return_value=utc_now):
            self.assertEqual(time_utils.beijing_now(), datetime(2026, 6, 5, 10, 30, 45))
            self.assertEqual(time_utils.beijing_now_iso(), "2026-06-05T10:30:45")


if __name__ == "__main__":
    unittest.main()
