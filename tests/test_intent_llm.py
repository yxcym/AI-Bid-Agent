"""LLM/hybrid 解析器离线单测：不发真实请求，模型响应全部 mock。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.config import LlmSection, SiteConfig
from app.intent.hybrid_parser import HybridIntentParser
from app.intent.llm_parser import LlmIntentParser, _extract_json
from app.intent.rule_parser import RuleIntentParser


def _site(code: str = "hefei_cg", name: str = "合肥公共资源交易中心·政府采购") -> SiteConfig:
    return SiteConfig(
        code=code,
        name=name,
        base_url="https://example.com",
        list_url="https://example.com/list",
    )


def _llm_with_response(payload: dict | str, calls: list) -> LlmIntentParser:
    """构造一个 _call 被 mock 掉的 LLM 解析器；calls 记录每次调用入参。"""
    p = LlmIntentParser(LlmSection(api_key="test-key"))
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def fake_call(query, sites, strict_retry=False):
        calls.append({"query": query, "strict_retry": strict_retry})
        return _extract_json(raw)

    p._call = fake_call
    return p


# ---------------------------------------------------------------- _extract_json

def test_extract_json_plain():
    assert _extract_json('{"keyword":"充电桩"}')["keyword"] == "充电桩"


def test_extract_json_with_fence():
    text = '```json\n{"keyword": "充电桩"}\n```'
    assert _extract_json(text)["keyword"] == "充电桩"


def test_extract_json_with_prose():
    text = '好的，解析结果如下：\n{"keyword": "充电桩", "region": "合肥"}\n以上。'
    assert _extract_json(text)["region"] == "合肥"


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        _extract_json("抱歉我无法解析这句话")


# ---------------------------------------------------------------- LlmIntentParser

def test_llm_disabled_without_key():
    assert not LlmIntentParser(LlmSection(api_key="")).enabled()


def test_llm_parse_builds_intent():
    p = _llm_with_response(
        {"keyword": "充电桩", "region": "上海", "start_date": None, "end_date": None,
         "cron": "0 9 * * *", "frequency_text": "每天9点"},
        calls=[],
    )
    intent = p.parse("上海的充电桩每天9点发我", [_site()])
    assert intent.keyword == "充电桩"
    assert intent.region == "上海"
    assert intent.cron == "0 9 * * *"
    assert intent.parser == "llm"


def test_llm_prompt_contains_today_and_sites():
    p = LlmIntentParser(LlmSection(api_key="k"))
    prompt = p._system_prompt([_site()])
    assert date.today().isoformat() in prompt      # 今天日期必须注入，否则相对时间算错
    assert "hefei_cg" in prompt                    # 站点清单必须注入


def test_llm_retry_on_bad_json():
    calls: list = []
    p = LlmIntentParser(LlmSection(api_key="test-key"))
    responses = ['我先想想：这不是JSON', '{"keyword":"充电桩"}']

    def fake_call(query, sites, strict_retry=False):
        calls.append(strict_retry)
        return _extract_json(responses[len(calls) - 1])

    p._call = fake_call
    intent = p.parse("合肥充电桩", [_site()])
    assert intent.keyword == "充电桩"
    assert calls == [False, True]                  # 第一次失败，第二次带 strict_retry


# ---------------------------------------------------------------- HybridIntentParser

def test_hybrid_strong_query_skips_llm():
    """规则全解出的标准句，绝不调模型——这是 hybrid 省钱的根基。"""
    calls: list = []
    llm = _llm_with_response({"keyword": "x"}, calls)
    h = HybridIntentParser(llm=llm)
    intent = h.parse("最近3个月的合肥充电桩招标信息", [_site()])
    assert calls == []                             # 一次都没调用
    assert intent.keyword == "充电桩"
    assert intent.region == "合肥"
    assert intent.start_date is not None


def test_hybrid_escapes_on_frequency_suspect():
    """口语频率（天亮就推一份）规则没解出 cron、关键词还被误判成短语 -> 升级模型补。"""
    calls: list = []
    llm = _llm_with_response(
        {"keyword": "充电桩", "region": "合肥", "cron": "0 6 * * *", "frequency_text": "天亮"},
        calls,
    )
    h = HybridIntentParser(llm=llm)
    intent = h.parse("合肥充电桩，天亮就给我推一份", [_site()])
    assert len(calls) == 1                         # 升级了一次
    assert intent.cron == "0 6 * * *"              # 模型补上的
    assert intent.keyword == "充电桩"              # 垃圾关键词被模型结果替换
    assert "frequency-suspect" in intent.parser


def test_hybrid_escapes_on_keyword_missing():
    calls: list = []
    llm = _llm_with_response({"keyword": "工程招标", "region": None, "cron": None, "frequency_text": None}, calls)
    h = HybridIntentParser(llm=llm)
    intent = h.parse("帮我把上个月的都整理一下发我", [_site()])
    assert len(calls) == 1
    assert intent.keyword == "工程招标"
    assert "keyword-missing" in intent.parser


def test_hybrid_never_overwrites_rule_fields():
    """模型只补空位，规则已有的值一律不动（模型说错也不能改写正确结果）。"""
    llm = _llm_with_response(
        {"keyword": "错误的词", "region": "北京", "cron": "0 23 * * *", "frequency_text": "错误频率"},
        calls=[],
    )
    h = HybridIntentParser(llm=llm)
    intent = h.parse("每天早上8点半的合肥充电桩招标信息", [_site()])
    assert intent.keyword == "充电桩"              # 规则结果保留
    assert intent.region == "合肥"
    assert intent.cron == "30 8 * * *"             # 规则解出的 cron 不被模型改写


def test_hybrid_falls_back_when_llm_raises():
    calls: list = []
    llm = _llm_with_response({"keyword": "x"}, calls)

    def boom(query, sites, strict_retry=False):
        raise ConnectionError("网络断了")

    llm._call = boom
    h = HybridIntentParser(llm=llm)
    intent = h.parse("合肥充电桩，天亮就给我推一份", [_site()])
    assert "rule-only" in intent.parser            # 明确标记降级
    assert intent.keyword == "天亮就给我推一份"    # 模型不可用时保持规则原始结果（宁错不编）


def test_hybrid_without_key_is_rule():
    h = HybridIntentParser(llm=LlmIntentParser(LlmSection(api_key="")))
    assert h.name == "hybrid(no-llm)"
    intent = h.parse("最近3个月的合肥充电桩招标信息", [_site()])
    assert intent.keyword == "充电桩"
