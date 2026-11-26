"""意图解析模块对外契约。

换解析器（规则 / 大模型 / 混合）时，只换实现，不动调用方。
"""

from __future__ import annotations

from typing import Protocol

from app.config import SiteConfig
from app.models import ParsedIntent


class IntentParser(Protocol):
    """一个解析器只要能吃一句话、吐出 ParsedIntent 就够了。"""

    name: str

    def parse(self, query: str, available_sites: list[SiteConfig]) -> ParsedIntent:
        ...
