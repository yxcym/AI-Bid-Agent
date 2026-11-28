"""规则解析器回归测试（第十六轮历史用例正式入库 + 时间/区域/关键词补强）。"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import SiteConfig
from app.intent.rule_parser import RuleIntentParser, _extract_frequency, _shift_months


def _p() -> RuleIntentParser:
    return RuleIntentParser()


def _site(code: str = "hefei_cg", name: str = "合肥公共资源交易中心·政府采购") -> SiteConfig:
    return SiteConfig(code=code, name=name, base_url="https://ggzy.hefei.gov.cn/", list_url="https://ggzy.hefei.gov.cn/list")


# ---------------------------------------------------------------- 频率（历史 8 例）

def test_freq_daily_with_period_and_half():
    """第十六轮修的静默 bug：每天早上8点半。"""
    text, cron = _extract_frequency("每天早上8点半")
    assert cron == "30 8 * * *"


def test_freq_weekly_with_period_and_half():
    text, cron = _extract_frequency("每周一早上8点半")
    assert cron == "30 8 * * 1"


def test_freq_monthly():
    text, cron = _extract_frequency("每月1号上午9点")
    assert cron == "0 9 1 * *"


def test_freq_daily_plain():
    text, cron = _extract_frequency("每天9点")
    assert cron == "0 9 * * *"


def test_freq_daily_evening_shifts_to_24h():
    text, cron = _extract_frequency("每天晚上8点")
    assert cron == "0 20 * * *"


def test_freq_hourly():
    text, cron = _extract_frequency("每小时")
    assert cron == "0 * * * *"


def test_freq_sunday_half():
    text, cron = _extract_frequency("每周日下午2点半")
    assert cron == "30 14 * * 0"


def test_freq_none_for_no_frequency():
    text, cron = _extract_frequency("最近3个月的合肥充电桩招标信息")
    assert cron is None and text is None


# ---------------------------------------------------------------- 公开入口 parse()

def test_parse_standard_query():
    intent = _p().parse("最近3个月的合肥充电桩招标信息", [_site()])
    assert intent.keyword == "充电桩"
    assert intent.region == "合肥"
    assert intent.cron is None
    assert intent.start_date == _shift_months(date.today(), -3)
    assert intent.end_date == date.today()


def test_parse_last_month():
    intent = _p().parse("上个月的合肥服务器采购", [_site()])
    today = date.today()
    last_prev_end = today.replace(day=1) - timedelta(days=1)
    assert intent.start_date == last_prev_end.replace(day=1)
    assert intent.end_date == last_prev_end


def test_parse_frequency_removed_from_keyword():
    """频率短语要被剥掉，不能污染关键词。"""
    intent = _p().parse("每天早上8点半的合肥充电桩招标信息", [_site()])
    assert intent.cron == "30 8 * * *"
    assert "早上" not in intent.keyword
    assert intent.keyword == "充电桩"


def test_parse_site_name_match():
    site = _site()
    intent = _p().parse("最近一个月合肥公共资源交易中心·政府采购的电梯招标", [site])
    assert intent.sites == ["hefei_cg"]


def test_parse_no_site_mention_passes_all_through():
    intent = _p().parse("最近一个月的电梯招标", [_site()])
    assert intent.sites == []


def test_parse_relative_date_days():
    intent = _p().parse("最近7天的合肥充电桩", [_site()])
    assert intent.start_date == date.today() - timedelta(days=7)
    assert intent.time_range_text == "最近7天"


# ---------------------------------------------------------------- 取词：剥泛词（第三十六轮）

def test_keyword_prefers_concrete_over_generic():
    """“充电站 建设 招标”以前取到“建设”，搜出来一堆不相关；现在取“充电站”。"""
    assert _p().parse("充电站 建设 招标", [_site()]).keyword == "充电站"


def test_keyword_strips_generic_tail():
    assert _p().parse("合肥高标准农田建设项目", [_site()]).keyword == "高标准农田"
    assert _p().parse("储能电站项目招标", [_site()]).keyword == "储能电站"
    assert _p().parse("最近1周的合肥服务器采购公告", [_site()]).keyword == "服务器"


def test_keyword_all_generic_returns_empty():
    """整句没有主题词时返回空，让上层提示用户，而不是拿去搜“公告”。"""
    assert _p().parse("帮我找一下最近的招标公告", [_site()]).keyword == ""


def test_keyword_keeps_multi_char_topic():
    """正常主题词不受影响。"""
    assert _p().parse("最近3个月的上海充电桩招标信息", [_site()]).keyword == "充电桩"
    assert _p().parse("最近一个月的电梯招标", [_site()]).keyword == "电梯"

