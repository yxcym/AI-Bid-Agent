"""领域模型：整个系统只在这几个结构之间传递数据。

刻意不用 ORM、不用复杂继承，全部是 dataclass —— 你在任何一步 print 都能看懂。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass
class ParsedIntent:
    """自然语言解析结果。一条用户原话 -> 这个结构。"""

    raw_query: str
    keyword: str = ""
    region: str | None = None
    # 时间范围的原文（如“最近3个月”），给人看
    time_range_text: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    # Quartz/APScheduler 的 cron 表达式，五段：分 时 日 月 周
    cron: str | None = None
    # 频率原文（如“每天9:00”）
    frequency_text: str | None = None
    # 指定站点 code；空列表表示用配置里全部启用站点
    sites: list[str] = field(default_factory=list)
    # 解析器名称，便于排查是规则命中还是模型解析
    parser: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_date"] = self.start_date.isoformat() if self.start_date else None
        data["end_date"] = self.end_date.isoformat() if self.end_date else None
        return data


@dataclass
class RawNotice:
    """抓取到的原始条目，还没清洗。"""

    source_code: str
    source_name: str
    title: str
    url: str
    publish_date: str = ""
    content: str = ""
    need_login: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Notice:
    """清洗后的条目，带指纹，可直接入文档。"""

    source_code: str
    source_name: str
    title: str
    url: str
    publish_date: str
    content: str
    fingerprint: str = ""
    is_new: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineResult:
    """一次流水线执行的全部结果，接口原样返回。"""

    query: str
    intent: ParsedIntent
    fetched: int = 0
    fresh: int = 0
    deduplicated: int = 0
    # 成功补抓到详情页正文的条数（0 表示全部沿用列表页摘要）
    detail_fetched: int = 0
    doc_path: str | None = None
    scheduled_job: str | None = None
    items: list[Notice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 中性提示（不是故障）：如「本版本未内置浏览器，某站点已跳过」。
    # 与 warnings 分开渲染，避免用户把形态差异读成抓取失败。
    notes: list[str] = field(default_factory=list)
    # 推送成功的收件方（如 "a@qq.com, b@163.com"）；失败进 warnings
    pushed_to: list[str] = field(default_factory=list)
    # 模型摘要：{overview, picks[{index,title,url,source_name,publish_date,why,risk}], model, ...}
    # 未启用/失败/无新增时为 None，前端据此隐藏摘要卡
    summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "intent": self.intent.to_dict(),
            "fetched": self.fetched,
            "fresh": self.fresh,
            "deduplicated": self.deduplicated,
            "detail_fetched": self.detail_fetched,
            "doc_path": self.doc_path,
            "scheduled_job": self.scheduled_job,
            "warnings": self.warnings,
            "notes": self.notes,
            "pushed_to": self.pushed_to,
            "summary": self.summary,
            "items": [n.to_dict() for n in self.items],
        }
