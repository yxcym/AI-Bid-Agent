"""HTTP 抓取实现：httpx + BeautifulSoup。

适用范围：公开列表页、无登录态、静态 HTML。
不适用范围：需要登录、强反爬、纯前端渲染 —— 这些留给 Playwright 侧车（第二阶段）。
"""

from __future__ import annotations

import time
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from app.config import AppConfig, SiteConfig
from app.crawler.base import LoginRequiredError
from app.models import ParsedIntent, RawNotice


class HttpCrawlerClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        if site.needs_login:
            raise LoginRequiredError(site)

        cfg = self._config.crawler
        notices: list[RawNotice] = []

        with httpx.Client(
            timeout=cfg.timeout_seconds,
            headers={"User-Agent": cfg.user_agent},
            follow_redirects=True,
        ) as client:
            for page in range(1, cfg.max_pages + 1):
                url = site.list_url.format(
                    keyword=quote(intent.keyword or ""),
                    page=page,
                )
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    # 单页失败不该让整轮任务挂掉
                    break

                # 中文站点编码常见 gb2312，httpx 有时猜错
                resp.encoding = resp.encoding or "utf-8"
                items = self._parse_list(site, resp.text)
                notices.extend(items)

                if len(notices) >= cfg.max_items_per_site:
                    break
                time.sleep(0.3)  # 轻限流，别把人家站点打疼

        return notices[: cfg.max_items_per_site]

    # ------------------------------------------------------ 内部

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
