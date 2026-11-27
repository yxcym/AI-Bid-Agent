"""大模型解析器（OpenAI 兼容协议）。

设计原则：模型只负责"把话拆成字段"，抓取、去重、出文档仍走确定性代码。
模型输出无法解析或网络不可用时，自动回退到规则解析器，绝不把整个链路拖挂。

凭据通道（优先级从高到低）：
  1. 环境变量 BIDAGENT_LLM_API_KEY / BIDAGENT_LLM_BASE_URL / BIDAGENT_LLM_MODEL
  2. config/secrets.yaml 的 intent.llm 段（gitignore，不进仓库）
  3. config.yaml 的 intent.llm 段（默认指向 DeepSeek，key 为空 = 未启用）

启用方式：config.yaml 里 intent.parser 改为 llm（全部走模型）或 hybrid（规则先行、模型补缺）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

import httpx

from app.common.errors import BizException
from app.config import LlmSection, SiteConfig
from app.intent.rule_parser import RuleIntentParser, _match_sites
from app.models import ParsedIntent

NAME = "llm"
log = logging.getLogger("bidagent")

# 免费模型（如智谱 Flash 系列）高峰期会返回 429 平台限流（code 1305），限流窗口为分钟级；
# 短退避多撞几次窗口能把高峰期成功率拉回来，试尽仍 429 则照常降级 rule-only。
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BACKOFF_SECONDS = 15.0

_PROMPT_TEMPLATE = """你是招投标信息检索的意图解析器。把用户的一句话拆成 JSON，只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。
今天的日期是 {today}。所有相对时间（如"最近3个月""上个月"）都必须以今天为基准换算成具体日期。

字段定义：
- keyword: 业务主题关键词，字符串，如"充电桩""服务器采购"。没有主题则空字符串
- region: 省市区域，如"合肥""上海"，没有则 null
- start_date: 起始日期 YYYY-MM-DD，没有明确时间则 null
- end_date: 截止日期 YYYY-MM-DD，没有则 null
- cron: 用户要求定时推送时给标准五段 cron（分 时 日 月 周，周日=0），否则 null。只有明确表达"每天/每周/每月几点"才给，"今天下午要"这类一次性要求给 null
- frequency_text: 频率的原文，没有则 null

可选站点清单（用户原话没有指向其中站点时给 []）：
{sites}

示例输入：最近的上海的充电桩招标信息，每天9点发我
示例输出：{{"keyword":"充电桩","region":"上海","start_date":null,"end_date":null,"cron":"0 9 * * *","frequency_text":"每天9点"}}"""


class LlmIntentParser:
    name = NAME

    def __init__(self, cfg: LlmSection | None = None) -> None:
        # cfg 未传时保持旧行为：纯环境变量（兼容脚本直接实例化）
        c = cfg or LlmSection()
        self.api_key = os.getenv("BIDAGENT_LLM_API_KEY", "").strip() or c.api_key.strip()
        self.base_url = os.getenv("BIDAGENT_LLM_BASE_URL", "").strip() or c.base_url
        self.model = os.getenv("BIDAGENT_LLM_MODEL", "").strip() or c.model
        self.timeout = float(os.getenv("BIDAGENT_LLM_TIMEOUT", "").strip() or c.timeout_seconds)
        self._fallback = RuleIntentParser()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def parse(self, query: str, available_sites: list[SiteConfig]) -> ParsedIntent:
        if not self.enabled():
            raise BizException("未配置大模型解析器：请设置 BIDAGENT_LLM_API_KEY，或把 intent.parser 改回 rule")

        try:
            payload = self._call(query, available_sites)
        except (ValueError, json.JSONDecodeError) as exc:
            # JSON 形态问题值得重试一次：让模型"只输出 JSON"再给一次机会
            log.warning("LLM 返回不可解析(%s)，重试一次", type(exc).__name__)
            payload = self._call(query, available_sites, strict_retry=True)
        return self._to_intent(query, payload, available_sites)

    # ------------------------------------------------------ 内部

    def _system_prompt(self, available_sites: list[SiteConfig]) -> str:
        site_lines = "\n".join(
            f"- {s.code}: {s.name}" for s in available_sites if s.enabled
        ) or "- （无可用站点）"
        return _PROMPT_TEMPLATE.format(today=date.today().isoformat(), sites=site_lines)

    def _call(self, query: str, available_sites: list[SiteConfig], strict_retry: bool = False) -> dict:
        url = self.base_url.rstrip("/") + "/chat/completions"
        user_content = query if not strict_retry else (
            f"{query}\n\n（补充说明：上一次你的回复无法解析。这一次只输出一个 JSON 对象，"
            "不要任何解释文字、不要 Markdown 代码块围栏。）"
        )
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": self._system_prompt(available_sites)},
                {"role": "user", "content": user_content},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = None
            for attempt in range(_RATE_LIMIT_RETRIES + 1):
                try:
                    resp = client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429 or attempt == _RATE_LIMIT_RETRIES:
                        raise
                    log.warning(
                        "LLM 限流(429)，%.0f 秒后重试（%d/%d）",
                        _RATE_LIMIT_BACKOFF_SECONDS, attempt + 1, _RATE_LIMIT_RETRIES,
                    )
                    time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
            content = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(content)

    def _to_intent(self, query: str, payload: dict, available_sites: list[SiteConfig]) -> ParsedIntent:
        start = _parse_date(payload.get("start_date")) or date.today().replace(day=1)
        end = _parse_date(payload.get("end_date")) or date.today()
        return ParsedIntent(
            raw_query=query,
            keyword=str(payload.get("keyword") or "").strip(),
            region=payload.get("region"),
            time_range_text=f"{start} ~ {end}",
            start_date=start,
            end_date=end,
            cron=payload.get("cron"),
            frequency_text=payload.get("frequency_text"),
            sites=_match_sites(query, available_sites),
            parser=self.name,
        )


def _extract_json(text: str) -> dict:
    text = text.strip()
    # 模型偶尔会包 ```json 围栏，或前后带客套话
    text = re.sub(r"^```(?:json)?\s*|```\s*$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("模型返回不含 JSON")
    return json.loads(m.group(0))


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
