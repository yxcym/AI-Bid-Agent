# -*- coding: utf-8 -*-
"""playwright 引擎与按站分流的单测（不真启动浏览器）。"""

from __future__ import annotations

import sys

import pytest

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlError, PlaywrightUnavailableError
from app.crawler.http_client import HttpCrawlerClient
from app.crawler.playwright_client import PlaywrightCrawlerClient
from app.crawler.registry import HybridCrawlerClient
from app.models import ParsedIntent


def _site(engine: str = "http", data_format: str = "html") -> SiteConfig:
    return SiteConfig(
        code="demo",
        name="演示站",
        base_url="https://demo.example.com",
        list_url="https://demo.example.com/list?kw={keyword}&page={page}",
        engine=engine,
        data_format=data_format,
    )


def test_engine_default_is_http():
    assert SiteConfig(code="x", name="x", base_url="https://a.com", list_url="https://a.com/l").engine == "http"


def test_hybrid_routes_by_engine(monkeypatch):
    """engine=playwright 的站点走 playwright 客户端，其余走 http 客户端。"""
    hits: list[str] = []
    monkeypatch.setattr(HttpCrawlerClient, "fetch", lambda self, site, intent: hits.append("http") or [])
    monkeypatch.setattr(PlaywrightCrawlerClient, "fetch", lambda self, site, intent: hits.append("pw") or [])

    client = HybridCrawlerClient(AppConfig())  # 构造不触网，两个子客户端只是持有配置
    client.fetch(_site("http"), ParsedIntent(raw_query="q"))
    client.fetch(_site("playwright"), ParsedIntent(raw_query="q"))
    client.fetch(_site("Playwright"), ParsedIntent(raw_query="q"))  # 大小写宽容
    assert hits == ["http", "pw", "pw"]


def test_playwright_json_rejected_before_import():
    """data_format=json + playwright 引擎：在碰浏览器之前就明确报错。"""
    client = PlaywrightCrawlerClient.__new__(PlaywrightCrawlerClient)
    with pytest.raises(CrawlError, match="仅支持 html"):
        client.fetch(_site("playwright", data_format="json"), ParsedIntent(raw_query="q"))


def test_playwright_unavailable_when_import_blocked(monkeypatch):
    """模拟环境没装 playwright：应抛 PlaywrightUnavailableError 而非裸 ImportError。"""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    client = PlaywrightCrawlerClient.__new__(PlaywrightCrawlerClient)
    with pytest.raises(PlaywrightUnavailableError, match="playwright"):
        client.fetch(_site("playwright"), ParsedIntent(raw_query="q"))


def test_short_keeps_first_line():
    from app.crawler.playwright_client import _short

    assert _short(RuntimeError("第一行\n第二行\n第三行")) == "第一行"
