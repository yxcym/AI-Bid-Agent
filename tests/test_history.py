"""运行历史的单测：记录、按日聚合补零、时区归属。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.pipeline.history import SqliteRunHistory

_TZ = ZoneInfo("Asia/Shanghai")


def _store(tmp_path) -> SqliteRunHistory:
    return SqliteRunHistory(tmp_path / "test.db", tz=_TZ)


def test_record_and_recent(tmp_path) -> None:
    h = _store(tmp_path)
    h.record("查询A", "充电桩", "合肥", fetched=8, fresh=3, deduplicated=5, has_doc=True, parser="hybrid", warnings=0)
    h.record("查询B", "服务器", None, fetched=10, fresh=0, deduplicated=10, has_doc=False, parser="rule", warnings=1)
    rows = h.recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["query"] == "查询B"  # 倒序
    assert rows[1]["fresh"] == 3
    assert rows[1]["has_doc"] == 1


def test_daily_pads_missing_days(tmp_path) -> None:
    h = _store(tmp_path)
    today = datetime.now(_TZ).date().isoformat()
    h.record("q", "kw", None, fetched=8, fresh=3, deduplicated=5, has_doc=True, parser="hybrid", warnings=0)
    days = h.daily(days=7)
    assert len(days) == 7
    assert days[-1]["date"] == today
    assert days[-1]["fresh"] == 3
    assert days[-1]["runs"] == 1
    # 缺数的日子补零而不是缺行
    assert all(d["runs"] == 0 for d in days[:-1])


def test_daily_aggregates_same_day(tmp_path) -> None:
    h = _store(tmp_path)
    for fresh in (2, 5):
        h.record("q", "kw", None, fetched=10, fresh=fresh, deduplicated=0, has_doc=False, parser="rule", warnings=0)
    days = h.daily(days=3)
    assert days[-1]["runs"] == 2
    assert days[-1]["fresh"] == 7


def test_daily_days_out_of_range_is_safe(tmp_path) -> None:
    h = _store(tmp_path)
    h.record("q", "kw", None, fetched=1, fresh=1, deduplicated=0, has_doc=False, parser="rule", warnings=0)
    assert len(h.daily(days=0)) == 1  # max(days,1) 兜底


def test_count(tmp_path) -> None:
    h = _store(tmp_path)
    assert h.count() == 0
    h.record("q", "kw", None, fetched=1, fresh=1, deduplicated=0, has_doc=False, parser="rule", warnings=0)
    assert h.count() == 1
