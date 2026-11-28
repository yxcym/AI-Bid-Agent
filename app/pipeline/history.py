"""运行历史：每轮流水线的执行记录，SQLite 一张表。

用途：给前端画「按天新增趋势」和展示最近几轮的走向。
日期归属用 Asia/Shanghai（配置里 schedule.timezone 的默认值）计算——
容器里系统时区是 UTC，直接拿 ts 分组会把晚上 8 点后的运行记到"明天"。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    run_date    TEXT,
    query       TEXT,
    keyword     TEXT,
    region      TEXT,
    fetched     INTEGER,
    fresh       INTEGER,
    deduplicated INTEGER,
    has_doc     INTEGER,
    parser      TEXT,
    warnings    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_history_date ON run_history(run_date);
"""

_TZ = ZoneInfo("Asia/Shanghai")


class SqliteRunHistory:
    def __init__(self, db_path: Path, tz: ZoneInfo = _TZ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._tz = tz
        self._init_schema()

    def record(
        self,
        query: str,
        keyword: str,
        region: str | None,
        fetched: int,
        fresh: int,
        deduplicated: int,
        has_doc: bool,
        parser: str,
        warnings: int,
    ) -> None:
        now = datetime.now(self._tz)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO run_history (ts, run_date, query, keyword, region, fetched, fresh, "
                "deduplicated, has_doc, parser, warnings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(timespec="seconds"),
                    now.date().isoformat(),
                    query,
                    keyword,
                    region,
                    fetched,
                    fresh,
                    deduplicated,
                    int(has_doc),
                    parser,
                    warnings,
                ),
            )

    def daily(self, days: int = 14) -> list[dict]:
        """按日聚合，缺数的日期补零，前端折线不用自己补。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_date, COUNT(1) AS runs, SUM(fetched) AS fetched, SUM(fresh) AS fresh, "
                "SUM(deduplicated) AS deduplicated, SUM(has_doc) AS docs "
                "FROM run_history WHERE run_date >= date(?, ?) GROUP BY run_date ORDER BY run_date",
                (
                    datetime.now(self._tz).date().isoformat(),
                    f"-{max(days, 1) - 1} days",
                ),
            ).fetchall()
        by_date = {r["run_date"]: dict(r) for r in rows}
        out = []
        from datetime import timedelta

        today = datetime.now(self._tz).date()
        for i in range(max(days, 1) - 1, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            r = by_date.get(d)
            out.append(
                {
                    "date": d,
                    "runs": r["runs"] if r else 0,
                    "fetched": r["fetched"] if r else 0,
                    "fresh": r["fresh"] if r else 0,
                    "deduplicated": r["deduplicated"] if r else 0,
                    "docs": r["docs"] if r else 0,
                }
            )
        return out

    def recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, query, keyword, fetched, fresh, deduplicated, has_doc, parser, warnings "
                "FROM run_history ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(1) FROM run_history").fetchone()[0])

    # ------------------------------------------------------ 内部

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
