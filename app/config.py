"""配置加载：config/config.yaml -> 强类型对象。

用 pydantic 校验而不是裸 dict，是为了配错一个字段就在启动时报错，
而不是跑到一半 NPE。
"""

from __future__ import annotations

import os
import re
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
    # 列表页模板，支持 {keyword} {page} {start_date} {end_date} 占位
    list_url: str
    needs_login: bool = False
    # 响应形态：html = 服务端直出网页（bs4 选择器）；json = AJAX 接口（字段映射）
    data_format: str = "html"
    # ---- html 模式 ----
    item_selector: str = "li"
    title_selector: str = "a"
    link_selector: str = "a"
    date_selector: str = ""
    content_selector: str = ""
    # ---- json 模式 ----
    # 条目数组的取值路径，点号分隔；路径中若遇 JSON 字符串会自动再解一层
    json_items_path: str = ""
    json_title_field: str = "title"
    json_date_field: str = ""
    # 直链字段；没有直链时用 json_url_template 按 {字段} / {字段.compact} 构造
    json_url_field: str = ""
    json_url_template: str = ""
    json_content_fields: list[str] = Field(default_factory=list)
    # 额外请求头（Referer 等），键值原样合并进 httpx 请求
    request_headers: dict[str, str] = Field(default_factory=dict)
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


class EmailPushSection(BaseModel):
    """SMTP 邮件推送。授权码等敏感项放 config/secrets.yaml（gitignore），启动时合并覆盖。"""

    enabled: bool = False
    # 留空则按发件邮箱域名自动推断（qq/163/126/gmail/outlook 等）
    smtp_host: str = ""
    smtp_port: int = 0  # 0 = 自动（465 走 SSL，587 走 STARTTLS）
    username: str = ""
    password: str = ""
    from_addr: str = ""  # 留空 = username
    to_addrs: list[str] = Field(default_factory=list)
    max_items_in_digest: int = 8
    timeout_seconds: int = 20


class PushSection(BaseModel):
    email: EmailPushSection = Field(default_factory=EmailPushSection)


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    intent: IntentSection = Field(default_factory=IntentSection)
    doc: DocSection = Field(default_factory=DocSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    push: PushSection = Field(default_factory=PushSection)

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


SECRETS_PATH = CONFIG_PATH.parent / "secrets.yaml"

# 环境变量通道：优先级最高，用于容器/CI/临时覆盖——凭据不落盘。
# 本机双击启动（启动.bat）拿不到临时 shell 变量，持久化需 setx（写入用户环境，随账户长期存在）。
ENV_SMTP_ENABLED = "BIDAGENT_SMTP_ENABLED"
ENV_SMTP_USERNAME = "BIDAGENT_SMTP_USERNAME"
ENV_SMTP_PASSWORD = "BIDAGENT_SMTP_PASSWORD"
ENV_SMTP_TO = "BIDAGENT_SMTP_TO"  # 逗号/分号/空白分隔，可多个收件人


def _env_push_overrides() -> dict:
    """只收集真正设置过的键，没设的键不参与覆盖（避免空字符串把文件里的值冲掉）。"""
    out: dict = {}
    raw_enabled = os.getenv(ENV_SMTP_ENABLED)
    if raw_enabled and raw_enabled.strip():
        out["enabled"] = raw_enabled.strip().lower() in {"1", "true", "yes", "on"}
    for env_name, key in ((ENV_SMTP_USERNAME, "username"), (ENV_SMTP_PASSWORD, "password")):
        value = os.getenv(env_name)
        if value and value.strip():
            out[key] = value.strip()
    raw_to = os.getenv(ENV_SMTP_TO)
    if raw_to and raw_to.strip():
        parts = [p for p in re.split(r"[,;，；\s]+", raw_to.strip()) if p]
        if parts:
            out["to_addrs"] = parts
    return out


def credential_source() -> str:
    """凭据当前来自哪里——启动日志与排障用，不打印具体值。"""
    if os.getenv(ENV_SMTP_PASSWORD, "").strip():
        return f"环境变量 {ENV_SMTP_PASSWORD}"
    if SECRETS_PATH.exists():
        return "config/secrets.yaml"
    return "config/config.yaml（明文占位，未配置真实凭据）"


def _deep_merge(base: dict, override: dict) -> dict:
    """secrets.yaml 只该有少数敏感键，深合并进主配置，其它结构不动。"""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> AppConfig:
    target = path or CONFIG_PATH
    if not target.exists():
        raise FileNotFoundError(f"配置文件不存在: {target}")
    with target.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    # 密钥覆盖层：config/secrets.yaml 存在则深合并（gitignore，不入库）
    if path is None and SECRETS_PATH.exists():
        with SECRETS_PATH.open("r", encoding="utf-8") as f:
            secrets = yaml.safe_load(f) or {}
        raw = _deep_merge(raw, secrets)
    # 环境变量优先级最高：显式指定路径（测试/多环境）时不读环境，保证可复现
    if path is None:
        env_overrides = _env_push_overrides()
        if env_overrides:
            raw = _deep_merge(raw, {"push": {"email": env_overrides}})
    return AppConfig.model_validate(raw)
