"""抓取客户端装配：mock 还是真实抓取、每站走 http 还是 playwright，只看配置。"""

from __future__ import annotations

from app.config import AppConfig, SiteConfig
from app.crawler.base import CrawlerClient
from app.crawler.http_client import HttpCrawlerClient
from app.crawler.mock_client import MockCrawlerClient
from app.crawler.playwright_client import PlaywrightCrawlerClient
from app.models import ParsedIntent, RawNotice


def build_crawler(config: AppConfig) -> CrawlerClient:
    if config.crawler.mock:
        return MockCrawlerClient(config)
    return HybridCrawlerClient(config)


class HybridCrawlerClient(CrawlerClient):
    """按站点的 engine 字段分流：默认 httpx 直连，配置了 playwright 的走无头浏览器。"""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._http = HttpCrawlerClient(config)
        self._playwright = PlaywrightCrawlerClient(config)

    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        engine = (site.engine or "http").lower()
        if engine == "playwright":
            return self._playwright.fetch(site, intent)
        return self._http.fetch(site, intent)
