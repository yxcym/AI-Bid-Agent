"""Playwright 渲染抓取：给被 WAF 拦 / JS 渲染的站点兜底。

设计取舍：
  * 继承 HttpCrawlerClient，只换传输层（浏览器拿 page.content()），解析层（选择器、
    urljoin、条目上限）全部复用——同一套 SiteConfig 字段两种引擎通用。
  * 惰性 import：环境没装 playwright 时抛 PlaywrightUnavailableError，
    PipelineService._safe_fetch 会把它记进 warnings，不拖垮其它站点。
  * 仅支持 html 形态；json 形态站点走直连 AJAX 接口即可，用不着浏览器。
"""

from __future__ import annotations

import time

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlError, PlaywrightUnavailableError
from app.crawler.http_client import HttpCrawlerClient
from app.models import ParsedIntent, RawNotice

_SETTLE_MS = 1200  # wait_for_selector 命中后的兜底渲染余量（毫秒）


class PlaywrightCrawlerClient(HttpCrawlerClient):
    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)

    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        if site.data_format != "html":
            raise CrawlError(f"站点[{site.name}]配置了 playwright 引擎但 data_format=json，仅支持 html 形态")

        try:
            from playwright.sync_api import Error as PWError
            from playwright.sync_api import TimeoutError as PWTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # exe 形态不带浏览器
            raise PlaywrightUnavailableError(site, str(exc)) from exc

        cfg = self._config.crawler
        notices: list[RawNotice] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=cfg.user_agent,
                    locale="zh-CN",
                    viewport={"width": 1440, "height": 900},
                )
                try:
                    page = context.new_page()
                    for page_num in range(1, cfg.max_pages + 1):
                        url = site.list_url.format(**self._format_map(site, intent, page_num))
                        try:
                            resp = page.goto(url, wait_until="domcontentloaded", timeout=cfg.timeout_seconds * 1000)
                        except (PWError, PWTimeoutError) as exc:
                            raise CrawlError(f"站点[{site.name}]第 {page_num} 页加载失败：{_short(exc)}") from exc
                        if resp is not None and resp.status >= 400:
                            raise CrawlError(f"站点[{site.name}]返回 HTTP {resp.status}（第 {page_num} 页）")

                        # 条目选择器出现即算渲染完成；等不到也照常解析（有些页面首屏就有）
                        try:
                            page.wait_for_selector(site.item_selector, timeout=5000)
                        except PWTimeoutError:
                            pass
                        page.wait_for_timeout(_SETTLE_MS)

                        notices.extend(self._parse_list(site, page.content()))
                        if len(notices) >= cfg.max_items_per_site:
                            break
                        time.sleep(0.3)
                finally:
                    context.close()
                    browser.close()
        except PlaywrightUnavailableError:
            raise
        except CrawlError:
            raise
        except Exception as exc:  # 浏览器启动失败等环境问题
            raise CrawlError(f"站点[{site.name}]playwright 引擎异常：{_short(exc)}") from exc

        return notices[: cfg.max_items_per_site]


def _short(exc: Exception) -> str:
    """playwright 报错动辄几十行堆栈，warnings 里只留第一行有信息量的。"""
    text = str(exc).strip().splitlines()
    return (text[0] if text else repr(exc))[:200]
