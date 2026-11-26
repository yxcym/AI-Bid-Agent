"""大模型解析器（预留，默认不启用）。

设计原则：模型只负责“把话拆成字段”，抓取、去重、出文档仍走确定性代码。
模型输出无法解析时，自动回退到规则解析器，绝不把整个链路拖挂。

启用方式：
  1. 配环境变量 BIDAGENT_LLM_API_KEY / BIDAGENT_LLM_BASE_URL / BIDAGENT_LLM_MODEL
  2. config.yaml 里 intent.parser 改为 llm
"""

from __future__ import annotations

import json
import os
import re
from datetime import date

import httpx

from app.common.errors import BizException
from app.config import SiteConfig
from app.intent.rule_parser import RuleIntentParser, _match_sites
from app.models import ParsedIntent

NAME = "llm"

SYSTEM_PROMPT = """你是招投标信息检索的意图解析器。把用户的一句话拆成 JSON，只输出 JSON，不要解释。
字段：
- keyword: 业务主题关键词，字符串，如“充电桩”“服务器采购”
- region: 省市区域，没有则 null
- start_date: 起始日期 YYYY-MM-DD，没有明确时间则 null
- end_date: 截止日期 YYYY-MM-DD，没有则 null
- cron: 如果用户要求定时推送，给出标准五段 cron（分 时 日 月 周，周日=0），否则 null
- frequency_text: 频率原文，没有则 null
示例输入：最近的上海的充电桩招标信息，每天9点发我
示例输出：{"keyword":"充电桩","region":"上海","start_date":null,"end_date":null,"cron":"0 9 * * *","frequency_text":"每天9点"}
"""


class LlmIntentParser:
    name = NAME

    def __init__(self) -> None:
        self.api_key = os.getenv("BIDAGENT_LLM_API_KEY", "")
        self.base_url = os.getenv("BIDAGENT_LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.model = os.getenv("BIDAGENT_LLM_MODEL", "deepseek-chat")
        self.timeout = float(os.getenv("BIDAGENT_LLM_TIMEOUT", "30"))
        self._fallback = RuleIntentParser()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def parse(self, query: str, available_sites: list[SiteConfig]) -> ParsedIntent:
        if not self.enabled():
            raise BizException("未配置大模型解析器：请设置 BIDAGENT_LLM_API_KEY，或把 intent.parser 改回 rule")

        try:
            payload = self._call(query)
            intent = self._to_intent(query, payload, available_sites)
            return intent
        except Exception as exc:  # 模型不可用不阻断业务
            fallback = self._fallback.parse(query, available_sites)
            fallback.parser = f"{NAME}-fallback({type(exc).__name__})"
            return fallback

    # ------------------------------------------------------ 内部

    def _call(self, query: str) -> dict:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
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
    # 模型偶尔会包 ```json
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
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
