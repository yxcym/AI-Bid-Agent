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
    """配置了 playwright 引擎但运行环境没有可用的浏览器。

    exe 形态不内置 Chromium，容器/开发环境装了才生效。
    这类情况不是「故障」而是「本版本不含」，上游记进 notes（中性提示）而不是 warnings（告警），
    避免用户把「轻量版没带浏览器」误读成「官网挂了 / 抓取出错」。
    """

    def __init__(self, site: SiteConfig, reason: str = "") -> None:
        self.site = site
        self.reason = reason
        super().__init__(self.short)

    @property
    def short(self) -> str:
        return f"「{self.site.name}」需要浏览器组件，本版本未内置，已跳过（Docker 版/完整版可用）"


class CrawlError(Exception):
    """playwright 抓取失败（导航被拒、超时、渲染异常），消息原样进 warnings。"""


class CrawlerClient(Protocol):
    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        ...
