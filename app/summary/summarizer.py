"""新增条目的模型摘要（阶段二 ②）。

定位是"帮你看一眼"，不是"替你判断"：
模型只做三件事——给一句总体判断、挑出最值得盯的几条、说明为什么和有什么坑。
**条目标题/链接/来源一律由代码按编号回填**，模型只允许输出编号，从结构上杜绝编造公告。
（这与 llm_parser 的"宁错不编"同一条纪律：模型不产出可核对的事实，只产出判断。）

任何失败——未配 key、超时、429、JSON 不可解析、挑不出有效条目——都抛 SummaryError，
由 pipeline 收进 warnings，文档与推送照常，绝不因摘要拖挂主链路。

凭据沿用 intent.llm（api_key/base_url/model），summary 段可单独覆盖；
要换更强的模型做摘要、解析仍用便宜的，只改 summary.model 一行。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date

import httpx

from app.config import AppConfig, LlmSection
from app.models import Notice, ParsedIntent

log = logging.getLogger(__name__)

# 与 llm_parser 同策略：免费模型高峰 429（code 1305），分钟级窗口，短退避多撞几次。
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_BACKOFF_SECONDS = 15.0

_CONTENT_IN_PROMPT = 180  # 每条正文截断长度：给模型够用的信息，又不把 prompt 撑爆


class SummaryError(Exception):
    """摘要不可用。消息面向用户，直接进 warnings。"""


_PROMPT_TEMPLATE = """你是招投标信息分析助手。今天是 {today}。
下面是本轮抓到的 {count} 条新增公告（编号 + 标题 + 来源 + 日期 + 正文节选）。
请做两件事：给一句总体判断，再挑出最值得盯的若干条。

只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{{"overview": "一两句话的总体判断：这批公告集中在什么方向、有没有值得注意的变化",
 "picks": [{{"index": 编号, "why": "为什么值得盯（说业务含义，不要复述标题）",
            "risk": "风险或要注意的点（工期紧/资格门槛高/预算异常/信息不全等，确实没有就写「未见明显风险」）"}}]}}

规则：
- picks 最多 {max_picks} 条，按重要程度排序；**宁可少挑也不要凑数**，不重要的就别选
- index 必须原样取自下文编号，不得自创编号；不得编造公告、金额或截止日期
- 正文节选里没有的信息就不要写，宁可写「信息不全」
- overview 不超过 120 字

公告列表：
{items}"""


class NoticeSummarizer:
    name = "summary"

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config.summary
        base = config.intent.llm
        # 环境变量 > summary 段显式值 > intent.llm 段，逐键回落
        self.api_key = (
            os.getenv("BIDAGENT_LLM_API_KEY", "").strip()
            or self._cfg.api_key.strip()
            or base.api_key.strip()
        )
        self.base_url = (
            os.getenv("BIDAGENT_LLM_BASE_URL", "").strip()
            or self._cfg.base_url.strip()
            or base.base_url
        )
        self.model = (
            os.getenv("BIDAGENT_LLM_MODEL", "").strip()
            or self._cfg.model.strip()
            or base.model
        )
        self.timeout = self._cfg.timeout_seconds or base.timeout_seconds

    def enabled(self) -> bool:
        return bool(self._cfg.enabled and self.api_key)

    # ------------------------------------------------------ 主入口

    def summarize(self, intent: ParsedIntent, notices: list[Notice]) -> dict:
        if not self.enabled():
            raise SummaryError("未启用摘要：config.yaml 的 summary.enabled 为 false，或未配置 LLM key")
        if not notices:
            raise SummaryError("本轮无新增条目，无需摘要")

        items = notices[: self._cfg.max_items_in_prompt]
        payload = self._call(intent, items)
        return self._to_summary(payload, items, len(notices))

    # ------------------------------------------------------ 调用

    def _items_block(self, notices: list[Notice]) -> str:
        lines = []
        for i, n in enumerate(notices, start=1):
            body = re.sub(r"\s+", " ", (n.content or "").strip())[:_CONTENT_IN_PROMPT]
            lines.append(
                f"[{i}] 标题：{n.title}\n"
                f"    来源：{n.source_name}　日期：{n.publish_date or '未标注'}\n"
                f"    节选：{body or '（无正文）'}"
            )
        return "\n".join(lines)

    def _call(self, intent: ParsedIntent, notices: list[Notice]) -> dict:
        url = self.base_url.rstrip("/") + "/chat/completions"
        system = _PROMPT_TEMPLATE.format(
            today=date.today().isoformat(),
            count=len(notices),
            max_picks=self._cfg.max_picks,
            items=self._items_block(notices),
        )
        user = f"用户原话：{intent.raw_query}\n关键词：{intent.keyword}"
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
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
                            "摘要模型限流(429)，%.0f 秒后重试（%d/%d）",
                            _RATE_LIMIT_BACKOFF_SECONDS, attempt + 1, _RATE_LIMIT_RETRIES,
                        )
                        time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                content = resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise SummaryError(
                f"摘要模型返回 HTTP {exc.response.status_code}（key 或额度问题，可先关掉 summary.enabled）"
            ) from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SummaryError(f"摘要模型调用失败：{type(exc).__name__}: {exc}") from exc

        try:
            return _extract_json(content)
        except ValueError as exc:
            raise SummaryError(f"摘要结果不可解析：{exc}") from exc

    # ------------------------------------------------------ 结果校验（把编号换回真实条目）

    def _to_summary(self, payload: dict, items: list[Notice], total: int) -> dict:
        overview = str(payload.get("overview") or "").strip()[:160]
        picks_raw = payload.get("picks")
        if not isinstance(picks_raw, list):
            picks_raw = []

        picks: list[dict] = []
        for raw in picks_raw:
            if not isinstance(raw, dict):
                continue
            idx = _as_index(raw.get("index"))
            if idx is None or not (1 <= idx <= len(items)):
                continue  # 编号不可信就丢弃，绝不按标题硬配
            notice = items[idx - 1]
            if any(p["index"] == idx for p in picks):
                continue
            picks.append(
                {
                    "index": idx,
                    "title": notice.title,
                    "url": notice.url,
                    "source_name": notice.source_name,
                    "publish_date": notice.publish_date,
                    "why": _clip(raw.get("why"), 120),
                    "risk": _clip(raw.get("risk"), 120) or "未见明显风险",
                }
            )
            if len(picks) >= self._cfg.max_picks:
                break

        if not picks and not overview:
            raise SummaryError("模型未给出可用摘要（既无总体判断也无有效条目）")

        return {
            "overview": overview,
            "picks": picks,
            "model": self.model,
            "items_considered": len(items),
            "items_total": total,
            "truncated": total > len(items),
        }


def _extract_json(text: str) -> dict:
    """模型偶尔包 ``` 围栏或带客套话，抠出第一个 JSON 对象。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|```\s*$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("模型返回不含 JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc


def _as_index(raw) -> int | None:
    """编号容错：接受 2 / "2" / "2." / "[2]" 这类写法，其余一律判无效。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        m = re.search(r"\d+", raw)
        return int(m.group(0)) if m else None
    return None


def _clip(raw, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    return text[:limit]
