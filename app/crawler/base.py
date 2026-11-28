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


class PlaywrightUnavailableError(Exception):
    """配置了 playwright 引擎但运行环境没装浏览器依赖。

    exe 形态不带 Chromium，容器/开发环境装了才生效；上游把它记进 warnings，不拖垮其它站点。
    """

    def __init__(self, site: SiteConfig, reason: str = "") -> None:
        tip = "pip install playwright && python -m playwright install chromium"
        super().__init__(f"站点[{site.name}]需要 playwright 引擎但环境不可用：{reason or tip}")
        self.site = site


class CrawlError(Exception):
    """playwright 抓取失败（导航被拒、超时、渲染异常），消息原样进 warnings。"""


class CrawlerClient(Protocol):
    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        ...
