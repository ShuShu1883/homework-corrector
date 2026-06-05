from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    return _utc_now().astimezone(BEIJING_TZ).replace(tzinfo=None)


def beijing_now_iso() -> str:
    return beijing_now().isoformat(timespec="seconds")
