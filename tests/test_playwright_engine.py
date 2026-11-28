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
    with pytest.raises(PlaywrightUnavailableError, match="浏览器组件"):
        client.fetch(_site("playwright"), ParsedIntent(raw_query="q"))


def test_short_keeps_first_line():
    from app.crawler.playwright_client import _short

    assert _short(RuntimeError("第一行\n第二行\n第三行")) == "第一行"


def test_browser_missing_recognized():
    """区分「本版本没装浏览器」与真故障：只有 Executable doesn't exist 才换算。"""
    from app.crawler.playwright_client import _is_browser_missing

    assert _is_browser_missing(RuntimeError("Executable doesn't exist at /tmp/chrome-headless-shell.exe"))
    assert not _is_browser_missing(RuntimeError("net::ERR_CONNECTION_RESET"))
    assert not _is_browser_missing(RuntimeError("Timeout 30000ms exceeded"))


def test_unavailable_short_is_plain_language():
    """面向用户的那句话不提技术栈、不出现「异常/失败」，别让人误读成官网挂了。"""
    site = _site("playwright")
    exc = PlaywrightUnavailableError(site, "Executable doesn't exist")
    assert site.name in exc.short
    for bad in ("异常", "失败", "Executable", "Traceback", "playwright"):
        assert bad not in exc.short
    assert str(exc) == exc.short


def test_unavailable_goes_to_notes_not_warnings():
    """形态差异走 notes（中性提示），不占 warnings（告警位）。"""
    from app.config import AppConfig
    from app.models import Notice, PipelineResult
    from app.pipeline.cleaner import RuleCleaner
    from app.pipeline.service import PipelineService

    class Boom:
        def fetch(self, site, intent):
            raise PlaywrightUnavailableError(site, "Executable doesn't exist")

        def fetch_details(self, site, notices, limit=None):
            raise PlaywrightUnavailableError(site, "Executable doesn't exist")

    svc = PipelineService.__new__(PipelineService)
    svc._config = AppConfig()
    svc._crawler = Boom()
    svc._cleaner = RuleCleaner(AppConfig())

    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    assert svc._safe_fetch(_site("playwright"), ParsedIntent(raw_query="q"), result) == ([], False)
    assert result.warnings == []
    assert len(result.notes) == 1 and "浏览器组件" in result.notes[0]

    fresh = [Notice(source_code="demo", source_name="演示站", title="标题", url="https://a.com/1", publish_date="", content="摘要")]
    asserts = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    svc._enrich_details(fresh, [_site("playwright")], asserts)
    assert asserts.warnings == []
    assert any("浏览器组件" in n for n in asserts.notes)


def test_browser_status_shape():
    """health 自证字段结构稳定（本机装了还是没装都不影响断言）。"""
    from app.crawler.playwright_client import browser_status

    status = browser_status()
    assert set(status) == {"available", "reason"}
    assert isinstance(status["available"], bool)
    assert isinstance(status["reason"], str)
