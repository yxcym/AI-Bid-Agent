"""Playwright 渲染抓取：给被 WAF 拦 / JS 渲染的站点兜底。

设计取舍：
  * 继承 HttpCrawlerClient，只换传输层（浏览器拿 page.content()），解析层（选择器、
    urljoin、条目上限）全部复用——同一套 SiteConfig 字段两种引擎通用。
  * 惰性 import：环境没装 playwright 时抛 PlaywrightUnavailableError，
    PipelineService._safe_fetch 会把它记进 notes（中性提示），不拖垮其它站点。
  * 浏览器缺失（Executable doesn't exist）与真故障分开：前者是形态差异，后者才告警。
  * 仅支持 html 形态；json 形态站点走直连 AJAX 接口即可，用不着浏览器。
"""

from __future__ import annotations

import time

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlError, PlaywrightUnavailableError
from app.crawler.extract import extract_main_text
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
            if _is_browser_missing(exc):
                raise PlaywrightUnavailableError(site, _short(exc)) from exc
            raise CrawlError(f"站点[{site.name}]playwright 引擎异常：{_short(exc)}") from exc

        return notices[: cfg.max_items_per_site]

    # ------------------------------------------------------ 详情页正文

    def fetch_details(
        self, site: SiteConfig, notices: list[RawNotice], limit: int | None = None
    ) -> list[str]:
        """详情页优先走 httpx（实测 ccgp 详情页不被 WAF 拦，比开浏览器快一个量级）。

        万一详情页也被拦（http 全部失败），才退回浏览器批量抓一次——
        一批只开一次浏览器，绝不逐条启停。
        """
        done = super().fetch_details(site, notices, limit)
        if done or not notices:
            return done
        return self._fetch_details_via_browser(site, notices, limit)

    def _fetch_details_via_browser(
        self, site: SiteConfig, notices: list[RawNotice], limit: int | None
    ) -> list[str]:
        cfg = self._config.crawler
        if not cfg.detail_enabled:
            return []
        cap = cfg.detail_max_per_site if limit is None else max(0, limit)
        done: list[str] = []

        try:
            from playwright.sync_api import Error as PWError
            from playwright.sync_api import TimeoutError as PWTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightUnavailableError(site, str(exc)) from exc

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                context = browser.new_context(user_agent=cfg.user_agent, locale="zh-CN", viewport={"width": 1440, "height": 900})
                try:
                    page = context.new_page()
                    for idx, notice in enumerate(notices[:cap]):
                        if idx:
                            time.sleep(cfg.detail_delay_seconds)
                        try:
                            resp = page.goto(notice.url, wait_until="domcontentloaded", timeout=cfg.timeout_seconds * 1000)
                        except (PWError, PWTimeoutError):
                            continue
                        if resp is None or resp.status >= 400:
                            continue
                        page.wait_for_timeout(600)
                        text = extract_main_text(page.content(), site.detail_selector, cfg.detail_max_chars)
                        if text and len(text) > len(notice.content):
                            notice.content = text
                            done.append(notice.url)
                finally:
                    context.close()
                    browser.close()
        except (PlaywrightUnavailableError, CrawlError):
            raise
        except Exception as exc:
            if _is_browser_missing(exc):
                raise PlaywrightUnavailableError(site, _short(exc)) from exc
            raise CrawlError(f"站点[{site.name}]详情页 playwright 抓取异常：{_short(exc)}") from exc

        return done


_BROWSER_MISSING_MARK = "Executable doesn't exist"


def _is_browser_missing(exc: Exception) -> bool:
    """区分「本版本没装浏览器」与「真故障」。

    playwright launch 找不到 chrome-headless-shell 时报 `Executable doesn't exist at ...`；
    这属于形态差异（exe 不内置 Chromium），不该按故障告警。
    """
    return _BROWSER_MISSING_MARK in str(exc)


def browser_status() -> dict:
    """探测浏览器组件是否就位，给 /api/health 自证用（exe 应为 false，容器应为 true）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"available": False, "reason": "playwright 包未安装"}
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:  # driver 起不来 / 目录缺文件
        return {"available": False, "reason": _short(exc)}
    import os

    if not path or not os.path.exists(path):
        return {"available": False, "reason": "浏览器文件缺失"}
    return {"available": True, "reason": ""}


def _short(exc: Exception) -> str:
    """playwright 报错动辄几十行堆栈，warnings 里只留第一行有信息量的。"""
    text = str(exc).strip().splitlines()
    return (text[0] if text else repr(exc))[:200]
