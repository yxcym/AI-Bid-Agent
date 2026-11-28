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
    """单站抓取失败（HTTP 非 2xx、超时、连接被拒、导航失败、渲染异常），消息面向用户，原样进 warnings。

    partial：失败之前已经抓到的条目。首页就失败时为空；翻到后面才失败时带着前面的结果，
    上游记一条告警后仍把这批并入本轮——「这一站没抓全」要看得见，到手的条目也不丢。
    """

    def __init__(self, msg: str, partial: list[RawNotice] | None = None) -> None:
        super().__init__(msg)
        self.partial: list[RawNotice] = list(partial or [])


class CrawlerClient(Protocol):
    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        ...
