"""混合解析器：规则先行，模型只补规则拿不准的字段。

为什么不是"全部走模型"：`最近3个月的合肥充电桩招标信息` 这类标准表达，规则解析
百分之百正确且零成本零延迟，没必要引入模型的随机性；真正需要模型的是
`帮我盯着那边充电桩，俩月内，天亮就推一份` 这类口语。所以升级条件必须保守，
让绝大多数请求根本不产生一次模型调用。

两类升级信号（可叠加）：
- keyword-missing：关键词为空，或是"给我推一份"这类动词短语被误当成关键词（垃圾词）
- frequency-suspect：原话疑似表达了频率（每天/几点/推送/发我…）但规则没解出 cron——
  这是"用户以为挂上了定时、实际没有"的静默事故源头，宁可多问一次模型

合并策略：模型只填规则结果里空缺/垃圾的字段；日期与站点判定始终以规则结果为准——
日期换算错了会直接导致抓错时间窗，宁可保守。模型不可用时整体退化为纯规则。
"""

from __future__ import annotations

import logging
import re

from app.config import SiteConfig
from app.intent.llm_parser import LlmIntentParser
from app.intent.rule_parser import RuleIntentParser
from app.models import ParsedIntent

NAME = "hybrid"
log = logging.getLogger("bidagent")

# 疑似频率表达的信号词。宽一点没关系——多升级一次只是几厘钱，漏升级才是真事故。
_FREQUENCY_SUSPECT = re.compile(
    r"每[天日周月]|周[一二三四五六日天]|点半|\d{1,2}点|\d{1,2}[:：]\d{2}"
    r"|定时|定期|推送|发送|发我|提醒|就给我|一份|天亮|早上|上午|下午|晚上|凌晨|清晨"
)

# 关键词里的动作词——出现即说明规则把"诉求短语"当成了"业务关键词"（垃圾词）
_ACTIONISH = re.compile(r"给我|发我|帮我|推送|发送|整理|一份|盯着|提醒|搜一|找一|汇总")


class HybridIntentParser:
    name = NAME

    def __init__(self, rule: RuleIntentParser | None = None, llm: LlmIntentParser | None = None) -> None:
        self._rule = rule or RuleIntentParser()
        self._llm = llm or LlmIntentParser()
        if not self._llm.enabled():
            # 无 key 时 hybrid 语义上等于 rule；保留名字便于从日志看出配置意图
            self.name = f"{NAME}(no-llm)"

    def parse(self, query: str, available_sites: list[SiteConfig]) -> ParsedIntent:
        base = self._rule.parse(query, available_sites)
        reasons = self._why(base, query)
        if not self._llm.enabled() or not reasons:
            return base

        try:
            hinted = self._llm.parse(query, available_sites)
        except Exception as exc:  # 网络/额度/超时——模型侧任何失败都不阻断
            log.warning("hybrid 升级 LLM 失败(%s)，按纯规则结果返回", type(exc).__name__)
            base.parser = f"{NAME}(rule-only,{type(exc).__name__})"
            return base
        return self._merge(base, hinted, reasons)

    # ------------------------------------------------------ 内部

    def _why(self, intent: ParsedIntent, query: str) -> str:
        """返回升级原因（可多个，'+' 连接）；空串表示不需要升级。"""
        found: list[str] = []
        if not intent.keyword or len(intent.keyword.strip()) < 2 or _ACTIONISH.search(intent.keyword):
            found.append("keyword-missing")
        if intent.cron is None and _FREQUENCY_SUSPECT.search(query):
            found.append("frequency-suspect")
        return "+".join(found)

    def _merge(self, base: ParsedIntent, hinted: ParsedIntent, reasons: str) -> ParsedIntent:
        """模型只补空位/垃圾位，规则已有的干净值一律不动。"""
        if not base.keyword or _ACTIONISH.search(base.keyword):
            base.keyword = hinted.keyword or base.keyword
        base.region = base.region or hinted.region
        base.cron = base.cron or hinted.cron
        base.frequency_text = base.frequency_text or hinted.frequency_text
        base.parser = f"{NAME}({reasons})"
        return base
