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

# ---- 冻结环境（PyInstaller 单文件 exe）路径分流 ----
# 只读资源（内嵌的 frontend/dist、配置模板）解包在 _MEIPASS；可写数据（data/、output/、
# 用户可编辑的 config/config.yaml、config/secrets.yaml）落 exe 旁——否则写到临时目录，
# 程序一退出就蒸发。
import sys

import shutil

_FROZEN = getattr(sys, "frozen", False)
if _FROZEN:
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    BASE_DIR = Path(sys.executable).resolve().parent
    CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
else:
    RESOURCE_ROOT = PROJECT_ROOT
    BASE_DIR = PROJECT_ROOT
    CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def ensure_user_config() -> str:
    """冻结模式首次运行：把内嵌模板复制到 exe 旁（用户可编辑、可持久化）。"""
    if not _FROZEN:
        return ""
    msgs: list[str] = []
    cfg_dir = BASE_DIR / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / "config.yaml"
    if not target.exists():
        shutil.copyfile(RESOURCE_ROOT / "config" / "config.yaml", target)
        msgs.append(f"已生成默认配置 {target}")
    example = cfg_dir / "secrets.yaml.example"
    if not example.exists():
        shutil.copyfile(RESOURCE_ROOT / "config" / "secrets.yaml.example", example)
    if msgs:
        print(*msgs, sep="\n")
    return "; ".join(msgs)


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


class LlmSection(BaseModel):
    """意图解析 LLM。key 属敏感项：放 config/secrets.yaml 的 intent.llm 下，或环境变量。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"  # OpenAI 兼容协议，豆包/千问只改这里
    model: str = "deepseek-chat"  # 豆包填推理接入点 ID（ep- 开头）
    timeout_seconds: float = 30.0


class IntentSection(BaseModel):
    # rule = 正则规则（默认，不烧钱不吃 key）
    # llm = 全部走大模型，不可用时自动回退 rule
    # hybrid = 规则先行，仅当规则拿不准（关键词缺失/疑似有频率但没解出 cron）才调模型补空缺
    parser: str = "rule"
    llm: LlmSection = Field(default_factory=LlmSection)


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


class WebhookChannelSection(BaseModel):
    """群机器人通道（飞书 / 钉钉）。webhook 地址与加签密钥属敏感项：
    放 config/secrets.yaml（gitignore）或环境变量，config.yaml 保持空壳。"""

    enabled: bool = False
    webhook_url: str = ""
    secret: str = ""  # 机器人「加签」密钥；未启用加签时留空
    max_items_in_digest: int = 5
    timeout_seconds: float = 15.0


class PushSection(BaseModel):
    email: EmailPushSection = Field(default_factory=EmailPushSection)
    feishu: WebhookChannelSection = Field(default_factory=WebhookChannelSection)
    dingtalk: WebhookChannelSection = Field(default_factory=WebhookChannelSection)


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
        return p if p.is_absolute() else BASE_DIR / p

    def enabled_sites(self) -> list[SiteConfig]:
        return [s for s in self.crawler.sites if s.enabled]


SECRETS_PATH = CONFIG_PATH.parent / "secrets.yaml"

# 环境变量通道：优先级最高，用于容器/CI/临时覆盖——凭据不落盘。
# 本机双击启动（启动.bat）拿不到临时 shell 变量，持久化需 setx（写入用户环境，随账户长期存在）。
ENV_SMTP_ENABLED = "BIDAGENT_SMTP_ENABLED"
ENV_SMTP_USERNAME = "BIDAGENT_SMTP_USERNAME"
ENV_SMTP_PASSWORD = "BIDAGENT_SMTP_PASSWORD"
ENV_SMTP_TO = "BIDAGENT_SMTP_TO"  # 逗号/分号/空白分隔，可多个收件人

ENV_LLM_API_KEY = "BIDAGENT_LLM_API_KEY"
ENV_LLM_BASE_URL = "BIDAGENT_LLM_BASE_URL"
ENV_LLM_MODEL = "BIDAGENT_LLM_MODEL"


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


def _env_llm_overrides() -> dict:
    """LLM 的环境变量通道：容器/CI 里最常用。没设的键不参与覆盖。"""
    out: dict = {}
    for env_name, key in (
        (ENV_LLM_API_KEY, "api_key"),
        (ENV_LLM_BASE_URL, "base_url"),
        (ENV_LLM_MODEL, "model"),
    ):
        value = os.getenv(env_name)
        if value and value.strip():
            out[key] = value.strip()
    return out


def llm_credential_source() -> str:
    """LLM key 当前来自哪里——启动日志与排障用，不打印具体值。"""
    if os.getenv(ENV_LLM_API_KEY, "").strip():
        return f"环境变量 {ENV_LLM_API_KEY}"
    if SECRETS_PATH.exists():
        try:
            with SECRETS_PATH.open("r", encoding="utf-8") as f:
                secrets = yaml.safe_load(f) or {}
            if (secrets.get("intent") or {}).get("llm", {}).get("api_key"):
                return "config/secrets.yaml"
        except yaml.YAMLError:
            pass
    return "未配置"


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
        # 冻结模式下用户删了 exe 旁的配置时，退回内嵌只读模板
        fallback = RESOURCE_ROOT / "config" / "config.yaml"
        if fallback.exists():
            target = fallback
        else:
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
        llm_overrides = _env_llm_overrides()
        if llm_overrides:
            raw = _deep_merge(raw, {"intent": {"llm": llm_overrides}})
    return AppConfig.model_validate(raw)
