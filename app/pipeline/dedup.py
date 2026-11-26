"""指纹去重：SQLite 一张表，够用且零运维。

赛题要的是“定时任务只推送增量”，所以这里的语义是：
  - 见过的指纹 -> 不算新增，不进本次文档
  - 第一次见的指纹 -> 入库并计入新增
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bid_fingerprint (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT,
    url         TEXT,
    source      TEXT,
    first_seen  TEXT
);
"""


class SqliteFingerprintStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        self._init_schema()

    def is_new(self, fp: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM bid_fingerprint WHERE fingerprint = ?", (fp,))
            return cur.fetchone() is None

    def save(self, fp: str, title: str, url: str, source: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO bid_fingerprint (fingerprint, title, url, source, first_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (fp, title, url, source, datetime.now().isoformat(timespec="seconds")),
            )

    def count(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(1) FROM bid_fingerprint")
            return int(cur.fetchone()[0])

    def clear(self) -> int:
        """清空指纹库。演示“首次全量 vs 二次增量”时用。"""
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(1) FROM bid_fingerprint")
            total = int(cur.fetchone()[0])
            conn.execute("DELETE FROM bid_fingerprint")
        return total

    # ------------------------------------------------------ 内部

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
