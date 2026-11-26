"""配置加载：config/config.yaml -> 强类型对象。

用 pydantic 校验而不是裸 dict，是为了配错一个字段就在启动时报错，
而不是跑到一半 NPE。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class SiteConfig(BaseModel):
    """一个招标信息站点的抓取参数。"""

    code: str
    name: str
    base_url: str
    # 列表页模板，支持 {keyword} {page} 占位
    list_url: str
    needs_login: bool = False
    # bs4 选择器；不同站点不一样，改这里就能扩站
    item_selector: str = "li"
    title_selector: str = "a"
    link_selector: str = "a"
    date_selector: str = ""
    content_selector: str = ""
    enabled: bool = True


class CrawlerConfig(BaseModel):
    mock: bool = True
    timeout_seconds: int = 20
    max_pages: int = 2
    max_items_per_site: int = 20
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
    sites: list[SiteConfig] = Field(default_factory=list)


class AppSection(BaseModel):
    name: str = "AI-Bid-Agent"
    port: int = 8000
    output_dir: str = "output"
    db_path: str = "data/bidagent.db"


class DocSection(BaseModel):
    # 中文用宋体，英文用 Times New Roman
    font_cn: str = "宋体"
    font_en: str = "Times New Roman"
    max_chars_per_item: int = 600


class ScheduleSection(BaseModel):
    enabled: bool = True
    timezone: str = "Asia/Shanghai"


class IntentSection(BaseModel):
    # rule | llm
    parser: str = "rule"


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    intent: IntentSection = Field(default_factory=IntentSection)
    doc: DocSection = Field(default_factory=DocSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)

    # ---- 路径解析：配置里写相对路径，统一落到项目根下 ----
    def output_path(self) -> Path:
        return self._resolve(self.app.output_dir)

    def db_path(self) -> Path:
        return self._resolve(self.app.db_path)

    @staticmethod
    def _resolve(raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def enabled_sites(self) -> list[SiteConfig]:
        return [s for s in self.crawler.sites if s.enabled]


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> AppConfig:
    target = path or CONFIG_PATH
    if not target.exists():
        raise FileNotFoundError(f"配置文件不存在: {target}")
    with target.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)
