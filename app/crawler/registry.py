"""抓取客户端装配：mock 还是真实 HTTP，只看配置一个开关。"""

from __future__ import annotations

from app.config import AppConfig
from app.crawler.base import CrawlerClient
from app.crawler.http_client import HttpCrawlerClient
from app.crawler.mock_client import MockCrawlerClient


def build_crawler(config: AppConfig) -> CrawlerClient:
    if config.crawler.mock:
        return MockCrawlerClient(config)
    return HttpCrawlerClient(config)
