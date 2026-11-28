# -*- coding: utf-8 -*-
"""抓取失败必须可见：站点 403/超时不能伪装成「本轮 0 条新增」；本机接口不能对任意网页开放跨域。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlError
from app.crawler.http_client import HttpCrawlerClient
from app.intent.rule_parser import RuleIntentParser
from app.models import ParsedIntent, PipelineResult, RawNotice
from app.pipeline.cleaner import RuleCleaner
from app.pipeline.dedup import SqliteFingerprintStore
from app.pipeline.service import PipelineService


def _site() -> SiteConfig:
    return SiteConfig(
        code="demo",
        name="演示站",
        base_url="https://demo.example.com",
        list_url="https://demo.example.com/list?kw={keyword}&page={page}",
    )


def _raw(n: int) -> list[RawNotice]:
    return [
        RawNotice(
            source_code="demo", source_name="演示站", title=f"标题{i}", url=f"https://a.com/{i}", publish_date="", content="摘要"
        )
        for i in range(n)
    ]


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.encoding = "utf-8"
        self.text = "<html></html>"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://demo.example.com"),
                response=httpx.Response(self.status_code),
            )


class _FakeClient:
    """按调用顺序返回预设响应，替代 httpx.Client（max_pages 默认 2，给两页足够）。"""

    def __init__(self, pages: list[_Resp]) -> None:
        self._pages = pages
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        resp = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return resp


# ---------------------------------------------------------------- http 抓取：失败要抛出来


def test_first_page_failure_raises_instead_of_empty(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient([_Resp(403)]))
    client = HttpCrawlerClient(AppConfig())
    with pytest.raises(CrawlError, match="403") as info:
        client.fetch(_site(), ParsedIntent(raw_query="q"))
    assert info.value.partial == []
    assert "演示站" in str(info.value)


def test_timeout_raises_with_reason(monkeypatch):
    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "Client", lambda **kw: _Boom())
    with pytest.raises(CrawlError, match="请求失败"):
        HttpCrawlerClient(AppConfig()).fetch(_site(), ParsedIntent(raw_query="q"))


def test_later_page_failure_keeps_earlier_items(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient([_Resp(200), _Resp(502)]))
    monkeypatch.setattr(HttpCrawlerClient, "_parse_list", lambda self, site, html: _raw(3))
    monkeypatch.setattr("app.crawler.http_client.time.sleep", lambda s: None)
    with pytest.raises(CrawlError, match="502") as info:
        HttpCrawlerClient(AppConfig()).fetch(_site(), ParsedIntent(raw_query="q"))
    assert len(info.value.partial) == 3


def test_success_path_unchanged(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient([_Resp(200), _Resp(200)]))
    monkeypatch.setattr(HttpCrawlerClient, "_parse_list", lambda self, site, html: _raw(2))
    monkeypatch.setattr("app.crawler.http_client.time.sleep", lambda s: None)
    assert len(HttpCrawlerClient(AppConfig()).fetch(_site(), ParsedIntent(raw_query="q"))) == 4


# ---------------------------------------------------------------- pipeline：0 新增要能分清两种原因


def _service(crawler, store) -> PipelineService:
    svc = PipelineService.__new__(PipelineService)  # 只测抓取→去重→提示这段，绕过完整装配
    svc._config = AppConfig()
    svc._crawler = crawler
    svc._cleaner = RuleCleaner(AppConfig())
    svc._parser = RuleIntentParser()
    svc._dedup = store
    svc._pushers = []
    svc._summarizer = None
    svc._history = None
    svc._pick_sites = lambda intent: [_site()]
    return svc


def test_all_sites_failed_is_not_reported_as_no_new(tmp_path):
    class Dead:
        def fetch(self, site, intent):
            raise CrawlError(f"站点[{site.name}]返回 HTTP 403（第 1 页）")

    out = _service(Dead(), SqliteFingerprintStore(tmp_path / "f.db")).run(
        "储能 电站 招标", write_doc=True, allow_schedule=False
    )
    assert out.fresh == 0 and out.doc_path is None
    assert any("没抓到" in w and "演示站" in w for w in out.warnings)
    assert any("不能据此判断今天没有新公告" in w for w in out.warnings)
    assert not any("本轮无新增条目" in w for w in out.warnings)


def test_zero_new_with_all_sites_ok_keeps_increment_note(tmp_path):
    class Empty:
        def fetch(self, site, intent):
            return []

    out = _service(Empty(), SqliteFingerprintStore(tmp_path / "f.db")).run(
        "储能 电站 招标", write_doc=True, allow_schedule=False
    )
    assert any("本轮无新增条目" in w for w in out.warnings)
    assert not any("没抓到" in w for w in out.warnings)


def test_partial_results_are_kept_and_flagged(tmp_path):
    class Half:
        def fetch(self, site, intent):
            raise CrawlError(f"站点[{site.name}]返回 HTTP 502（第 2 页）", partial=_raw(5))

    svc = _service(Half(), SqliteFingerprintStore(tmp_path / "f.db"))
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    items, failed = svc._safe_fetch(_site(), ParsedIntent(raw_query="q"), result)
    assert failed is True and len(items) == 5
    assert "仍计入本轮" in result.warnings[0]


def test_browser_missing_is_not_counted_as_failed_site(tmp_path):
    from app.crawler.base import PlaywrightUnavailableError

    class NoBrowser:
        def fetch(self, site, intent):
            raise PlaywrightUnavailableError(site, "Executable doesn't exist")

    svc = _service(NoBrowser(), SqliteFingerprintStore(tmp_path / "f.db"))
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    _, failed = svc._safe_fetch(_site(), ParsedIntent(raw_query="q"), result)
    assert failed is False and result.warnings == [] and len(result.notes) == 1


# ---------------------------------------------------------------- 加固：无 CORS、health 自报数据目录


def test_no_cors_middleware_mounted():
    """本机接口无鉴权，不能让浏览器里打开的任意网页跨域调用；三种跑法都同源，用不着 CORS。"""
    from app.main import app

    mounted = [m.cls for m in app.user_middleware]
    assert all(m.__name__ != "CORSMiddleware" for m in mounted)


def test_health_reports_data_dir(tmp_path):
    """本机同时跑容器(:80)与 exe(:8000)时各有一份指纹库，health 要能说清当前是哪一套。"""
    from app.api.routes import health

    cfg = AppConfig()
    cfg.app.db_path = str(tmp_path / "数据甲" / "x.db")
    ctx = SimpleNamespace(
        config=cfg,
        parser=SimpleNamespace(name="rule"),
        dedup=SimpleNamespace(count=lambda: 0),
        scheduler=SimpleNamespace(list_jobs=lambda: []),
        pusher=SimpleNamespace(status=lambda: {"enabled": False}),
        webhook=SimpleNamespace(status=lambda: []),
        summarizer=SimpleNamespace(enabled=lambda: False, model=None),
        history=SimpleNamespace(count=lambda: 0),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)))

    data = health(request)["data"]
    assert data["data_dir"] == str(tmp_path / "数据甲")
