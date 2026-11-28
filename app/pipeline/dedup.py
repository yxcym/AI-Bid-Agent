"""指纹去重：SQLite 一张表，够用且零运维。

赛题要的是“定时任务只推送增量”，所以这里的语义是：
  - 见过的指纹 -> 不算新增，不进本次文档
  - 第一次见的指纹 -> 入库并计入新增

指纹库是**全局**的（不分查询、不分订阅）：同一条公告不论出现在哪次查询里，
只会被计为新增一次。这是单用户工具的免打扰设计，README 有写明。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
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

    def add(self, fp: str, title: str, url: str, source: str) -> bool:
        """判新与入库一步完成，返回「是否为新增」。

        靠 INSERT OR IGNORE 的 rowcount 判新：判与写在同一条语句里，SQLite 的写锁保证
        两个并发 run（定时 + 手动）同时遇到同一指纹时只有一边得到 1。
        原先「先 SELECT 再 INSERT」走两次独立连接，中间的窗口可被并发插队——
        两边都判新，重复出文档、重复推送。
        """
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO bid_fingerprint (fingerprint, title, url, source, first_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (fp, title, url, source, datetime.now().isoformat(timespec="seconds")),
            )
            return cur.rowcount == 1

    def count(self) -> int:
        with closing(self._connect()) as conn:
            cur = conn.execute("SELECT COUNT(1) FROM bid_fingerprint")
            return int(cur.fetchone()[0])

    def clear(self) -> int:
        """清空指纹库。演示“首次全量 vs 二次增量”时用。"""
        with closing(self._connect()) as conn, conn:
            total = int(conn.execute("SELECT COUNT(1) FROM bid_fingerprint").fetchone()[0])
            conn.execute("DELETE FROM bid_fingerprint")
        return total

    # ------------------------------------------------------ 内部

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        # `with conn` 只管提交/回滚、不关连接；外面再套 closing，长跑进程才不会攒句柄
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)
