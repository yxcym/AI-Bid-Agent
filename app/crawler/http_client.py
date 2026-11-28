"""HTTP 抓取实现：httpx + BeautifulSoup + JSON 字段映射。

两种站点形态：
  * html —— 服务端直出列表页，用 CSS 选择器抽条目
  * json —— 前端 AJAX 数据接口，按字段路径抽条目（详情链接可直取或按模板构造）

适用范围：公开列表页、无登录态。
不适用范围：需要登录、强反爬 —— 这些留给 Playwright 侧车（后续阶段）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlError, LoginRequiredError
from app.crawler.extract import extract_main_text
from app.models import ParsedIntent, RawNotice

_PLACEHOLDER_RE = re.compile(r"\{(\w+)(?:\.(compact))?\}")


def _first_line(exc: Exception) -> str:
    """httpx 的异常常带多行详情，warnings 里只留第一行。"""
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]


class HttpCrawlerClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        if site.needs_login:
            raise LoginRequiredError(site)

        cfg = self._config.crawler
        notices: list[RawNotice] = []
        headers = {"User-Agent": cfg.user_agent, **site.request_headers}

        with httpx.Client(
            timeout=cfg.timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for page in range(1, cfg.max_pages + 1):
                url = site.list_url.format(**self._format_map(site, intent, page))
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # 不能 break 了事：静默返回空列表会让「站点 403」在用户眼里变成「本轮 0 条新增」
                    raise CrawlError(
                        f"站点[{site.name}]返回 HTTP {exc.response.status_code}（第 {page} 页）", partial=notices
                    ) from exc
                except httpx.HTTPError as exc:
                    raise CrawlError(f"站点[{site.name}]请求失败（第 {page} 页）：{_first_line(exc)}", partial=notices) from exc

                # 中文站点编码常见 gb2312，httpx 有时猜错
                resp.encoding = resp.encoding or "utf-8"
                if site.data_format == "json":
                    items = self._parse_json(site, resp)
                else:
                    items = self._parse_list(site, resp.text)
                notices.extend(items)

                if len(notices) >= cfg.max_items_per_site:
                    break
                time.sleep(0.3)  # 轻限流，别把人家站点打疼

        return notices[: cfg.max_items_per_site]

    # ------------------------------------------------------ 详情页正文

    def fetch_details(
        self, site: SiteConfig, notices: list[RawNotice], limit: int | None = None
    ) -> list[str]:
        """给列表页条目补抓详情页正文，返回成功补到正文的 URL 列表。

        只换 content（且新正文要比原来长才换），失败静默跳过——调用方按
        "尝试/成功"差额记一条汇总 warning，不逐条刷屏。
        """
        cfg = self._config.crawler
        if not cfg.detail_enabled or not notices:
            return []
        cap = cfg.detail_max_per_site if limit is None else max(0, limit)
        headers = {"User-Agent": cfg.user_agent, **site.request_headers}
        done: list[str] = []

        with httpx.Client(
            timeout=cfg.timeout_seconds,
            headers=headers,
            follow_redirects=True,
        ) as client:
            for idx, notice in enumerate(notices[:cap]):
                if idx:  # 间隔放开头：上一条失败也要慢下来，别连发
                    time.sleep(cfg.detail_delay_seconds)
                try:
                    resp = client.get(notice.url)
                    resp.raise_for_status()
                    text = extract_main_text(resp.text, site.detail_selector, cfg.detail_max_chars)
                except httpx.HTTPError:
                    continue
                if text and len(text) > len(notice.content):
                    notice.content = text
                    done.append(notice.url)

        return done

    # ------------------------------------------------------ 内部

    @staticmethod
    def _format_map(site: SiteConfig, intent: ParsedIntent, page: int) -> dict[str, Any]:
        """list_url 模板的占位符取值。缺省的日期给空串，别让 format 炸键。"""
        return {
            "keyword": quote(intent.keyword or ""),
            "page": page,
            "start_date": intent.start_date.isoformat() if intent.start_date else "",
            "end_date": intent.end_date.isoformat() if intent.end_date else "",
        }

    # ---- html 模式（服务端直出列表页） ----

    def _parse_list(self, site: SiteConfig, html: str) -> list[RawNotice]:
        soup = BeautifulSoup(html, "lxml")
        result: list[RawNotice] = []

        for node in soup.select(site.item_selector)[: self._config.crawler.max_items_per_site]:
            title_node = node.select_one(site.title_selector)
            if not title_node:
                continue
            title = title_node.get_text(strip=True)
            link = title_node.get("href") or ""
            if site.link_selector and site.link_selector != site.title_selector:
                link_node = node.select_one(site.link_selector)
                link = (link_node.get("href") if link_node else "") or link
            if not title or not link:
                continue

            publish_date = ""
            if site.date_selector:
                date_node = node.select_one(site.date_selector)
                if date_node:
                    publish_date = date_node.get_text(strip=True)

            result.append(
                RawNotice(
                    source_code=site.code,
                    source_name=site.name,
                    title=title,
                    url=urljoin(site.base_url, link),
                    publish_date=publish_date,
                    content=node.get_text(" ", strip=True),
                    need_login=site.needs_login,
                )
            )
        return result

    # ---- json 模式（AJAX 数据接口） ----

    def _parse_json(self, site: SiteConfig, resp: httpx.Response) -> list[RawNotice]:
        data = resp.json()
        items = self._walk_path(data, site.json_items_path)
        if not isinstance(items, list):
            return []

        result: list[RawNotice] = []
        for item in items[: self._config.crawler.max_items_per_site]:
            if not isinstance(item, dict):
                continue
            title = str(item.get(site.json_title_field) or "").strip()
            if not title:
                continue

            url = self._build_item_url(site, item)
            if not url:
                continue

            parts = [str(item.get(f) or "").strip() for f in site.json_content_fields]
            content = "；".join(p for p in parts if p)

            result.append(
                RawNotice(
                    source_code=site.code,
                    source_name=site.name,
                    title=title,
                    url=url,
                    publish_date=str(item.get(site.json_date_field) or "").strip(),
                    content=content,
                    need_login=site.needs_login,
                )
            )
        return result

    @staticmethod
    def _walk_path(data: Any, path: str) -> Any:
        """按点号路径取值；路径中遇到 JSON 字符串（接口常见双层封装）自动再解一层。"""
        node = data
        for seg in filter(None, path.split(".")):
            if isinstance(node, dict):
                node = node.get(seg)
            elif isinstance(node, list) and seg.isdigit():
                idx = int(seg)
                node = node[idx] if idx < len(node) else None
            else:
                return None
            if isinstance(node, str):
                stripped = node.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        node = json.loads(stripped)
                    except json.JSONDecodeError:
                        return None
            if node is None:
                return None
        return node

    @staticmethod
    def _build_item_url(site: SiteConfig, item: dict) -> str:
        if site.json_url_field:
            raw = str(item.get(site.json_url_field) or "").strip()
            return urljoin(site.base_url, raw) if raw else ""

        if not site.json_url_template:
            return ""

        def _sub(match: re.Match) -> str:
            field, compact = match.group(1), match.group(2)
            value = str(item.get(field) or "")
            if compact:
                # “2026-11-25” -> “20261125”，用于拼详情页路径
                value = re.sub(r"\W", "", value)
            return value

        path = _PLACEHOLDER_RE.sub(_sub, site.json_url_template)
        return urljoin(site.base_url, path)
