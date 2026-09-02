"""数据库与持久化层统一使用的 UTC 时间源。"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
