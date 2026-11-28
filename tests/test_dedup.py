# -*- coding: utf-8 -*-
"""指纹库：判新与入库必须是同一条语句，连接必须真的关掉。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.pipeline.dedup import SqliteFingerprintStore


def test_add_is_idempotent(tmp_path):
    store = SqliteFingerprintStore(tmp_path / "f.db")
    assert store.add("fp1", "标题", "https://a.com/1", "站点") is True
    assert store.add("fp1", "标题", "https://a.com/1", "站点") is False
    assert store.count() == 1


def test_concurrent_same_fingerprint_exactly_one_new(tmp_path):
    """定时任务与手动点击同时遇到同一公告：只能有一边判新，否则重复出文档、重复推送。"""
    store = SqliteFingerprintStore(tmp_path / "f.db")
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: store.add("fp-same", "t", "u", "s"), range(16)))
    assert sum(outcomes) == 1
    assert store.count() == 1


def test_clear_returns_removed_count(tmp_path):
    store = SqliteFingerprintStore(tmp_path / "f.db")
    for i in range(3):
        store.add(f"fp{i}", "t", "u", "s")
    assert store.clear() == 3
    assert store.count() == 0


def test_connections_are_closed(tmp_path, monkeypatch):
    """`with sqlite3.connect()` 只提交不关闭；长跑进程靠 closing 才不攒句柄。"""
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def spy(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", spy)
    store = SqliteFingerprintStore(tmp_path / "f.db")
    store.add("fp1", "t", "u", "s")
    store.count()
    store.clear()

    assert opened, "应当至少建立过一次连接"
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
