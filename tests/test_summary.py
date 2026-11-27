"""模型摘要单测：编号回填、非法编号丢弃、上限、降级语义。

核心纪律：模型只准输出编号，标题/链接由代码回填——所以"编号越界"必须被丢弃而不是猜。
"""

from __future__ import annotations

import httpx
import pytest

from app.config import AppConfig, LlmSection, SummarySection
from app.models import Notice, ParsedIntent
from app.summary.summarizer import NoticeSummarizer, SummaryError

# ---------------------------------------------------------------- 夹具


def _config(**kw) -> AppConfig:
    cfg = AppConfig()
    cfg.summary = SummarySection(enabled=kw.pop("enabled", True), **kw)
    cfg.intent.llm = LlmSection(api_key="sk-test", base_url="https://llm.invalid/v1", model="test-model")
    return cfg


def _notices(n: int = 3) -> list[Notice]:
    return [
        Notice(
            source_code="hefei_gc",
            source_name="合肥公共资源交易中心·工程建设",
            title=f"第{i}标段 充电桩设备采购",
            url=f"https://ggzy.hefei.gov.cn/jyxx/{i}.html",
            publish_date="2026-11-27",
            content="预算 120 万元，投标截止 12 月 5 日。" * 3,
        )
        for i in range(1, n + 1)
    ]


def _intent() -> ParsedIntent:
    return ParsedIntent(raw_query="最近1个月的合肥充电桩招标信息", keyword="充电桩", region="合肥")


class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://x"), response=self)

    def json(self) -> dict:
        return self._payload


def _patch(monkeypatch, content: str, status_code: int = 200) -> None:
    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

        def post(self, url, json=None, headers=None):
            return _FakeResp({"choices": [{"message": {"content": content}}]}, status_code)

    monkeypatch.setattr("app.summary.summarizer.httpx.Client", _Client)


# ---------------------------------------------------------------- 启用判定


def test_disabled_without_key() -> None:
    cfg = _config()
    cfg.intent.llm = LlmSection(api_key="")
    assert NoticeSummarizer(cfg).enabled() is False


def test_disabled_by_switch() -> None:
    assert NoticeSummarizer(_config(enabled=False)).enabled() is False


def test_model_override_wins_over_intent_llm() -> None:
    s = NoticeSummarizer(_config(model="strong-model"))
    assert s.model == "strong-model"


def test_model_falls_back_to_intent_llm() -> None:
    assert NoticeSummarizer(_config()).model == "test-model"


# ---------------------------------------------------------------- 编号回填


def test_picks_are_resolved_to_real_notices(monkeypatch) -> None:
    content = """```json
{"overview":"这批集中在充电桩设备，预算中等。",
 "picks":[{"index":2,"why":"第二标段预算最高","risk":"截止时间仅剩一周"}]}
```"""
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert summary["overview"].startswith("这批集中在")
    pick = summary["picks"][0]
    # 标题/链接来自真实条目，不是模型编的
    assert pick["title"] == "第2标段 充电桩设备采购"
    assert pick["url"].endswith("/2.html")
    assert pick["risk"] == "截止时间仅剩一周"


def test_out_of_range_index_is_dropped(monkeypatch) -> None:
    content = '{"overview":"x","picks":[{"index":9,"why":"越界"},{"index":1,"why":"正常"}]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert [p["index"] for p in summary["picks"]] == [1]


def test_index_as_string_is_accepted(monkeypatch) -> None:
    content = '{"overview":"x","picks":[{"index":"[2]","why":"w"}]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert summary["picks"][0]["index"] == 2


def test_duplicate_index_kept_once(monkeypatch) -> None:
    content = '{"overview":"x","picks":[{"index":1,"why":"a"},{"index":1,"why":"b"}]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert len(summary["picks"]) == 1


def test_picks_capped_by_max_picks(monkeypatch) -> None:
    content = '{"overview":"x","picks":[{"index":1,"why":"a"},{"index":2,"why":"b"},{"index":3,"why":"c"}]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config(max_picks=2)).summarize(_intent(), _notices())
    assert len(summary["picks"]) == 2


def test_missing_risk_gets_default_text(monkeypatch) -> None:
    content = '{"overview":"x","picks":[{"index":1,"why":"w","risk":""}]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert summary["picks"][0]["risk"] == "未见明显风险"


def test_truncation_flag_when_items_exceed_prompt_limit(monkeypatch) -> None:
    content = '{"overview":"x","picks":[]}'
    _patch(monkeypatch, content)
    summary = NoticeSummarizer(_config(max_items_in_prompt=2)).summarize(_intent(), _notices(5))
    assert summary["items_considered"] == 2
    assert summary["items_total"] == 5
    assert summary["truncated"] is True


# ---------------------------------------------------------------- 降级语义


def test_empty_picks_and_overview_raises(monkeypatch) -> None:
    _patch(monkeypatch, '{"overview":"","picks":[]}')
    with pytest.raises(SummaryError):
        NoticeSummarizer(_config()).summarize(_intent(), _notices())


def test_non_json_raises(monkeypatch) -> None:
    _patch(monkeypatch, "我觉得这批项目都挺好的。")
    with pytest.raises(SummaryError):
        NoticeSummarizer(_config()).summarize(_intent(), _notices())


def test_http_error_becomes_summary_error(monkeypatch) -> None:
    _patch(monkeypatch, "{}", status_code=401)
    with pytest.raises(SummaryError) as ei:
        NoticeSummarizer(_config()).summarize(_intent(), _notices())
    assert "401" in str(ei.value)


def test_no_notices_raises() -> None:
    with pytest.raises(SummaryError):
        NoticeSummarizer(_config()).summarize(_intent(), [])


def test_disabled_raises_before_any_call() -> None:
    with pytest.raises(SummaryError):
        NoticeSummarizer(_config(enabled=False)).summarize(_intent(), _notices())
