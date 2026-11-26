"""抓取模块对外契约。换 httpx / Playwright / 第三方 API，都只实现这个接口。"""

from __future__ import annotations

from typing import Protocol

from app.config import SiteConfig
from app.models import ParsedIntent, RawNotice


class LoginRequiredError(Exception):
    """站点需要登录态。HTTP 抓取遇到它不静默失败，而是明确上报。"""

    def __init__(self, site: SiteConfig) -> None:
        super().__init__(f"站点[{site.name}]需要登录态，请启用 Playwright 侧车")
        self.site = site


class CrawlerClient(Protocol):
    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        ...
