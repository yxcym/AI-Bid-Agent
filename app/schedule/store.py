"""定时任务持久化：SQLite 一张表，进程重启后恢复注册。

为什么不用 APScheduler 的 SQLAlchemyJobStore：那要引入 sqlalchemy 依赖，
而这里要恢复的全部信息只有 (job_id, query, cron) 三列——原话进，cron 出，
启动时逐条重新 add 即可，next_run 由调度器自己算。零新依赖，够用且可审查。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bid_job (
    job_id     TEXT PRIMARY KEY,
    query      TEXT NOT NULL,
    cron       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SqliteJobStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._init_schema()

    def save(self, job_id: str, query: str, cron: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bid_job (job_id, query, cron, created_at) "
                "VALUES (?, ?, ?, ?)",
                (job_id, query, cron, datetime.now().isoformat(timespec="seconds")),
            )

    def delete(self, job_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM bid_job WHERE job_id = ?", (job_id,))
            return cur.rowcount > 0

    def all(self) -> list[tuple[str, str, str]]:
        with self._connect() as conn:
            cur = conn.execute("SELECT job_id, query, cron FROM bid_job ORDER BY created_at")
            return [(r["job_id"], r["query"], r["cron"]) for r in cur.fetchall()]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
