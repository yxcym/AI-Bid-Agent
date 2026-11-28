# -*- coding: utf-8 -*-
"""详情页正文抽取与补抓编排的单测（不联网）。"""

from __future__ import annotations

from app.config import AppConfig, SiteConfig
from app.crawler.extract import extract_main_text
from app.models import Notice, PipelineResult, ParsedIntent
from app.pipeline.cleaner import RuleCleaner
from app.pipeline.service import PipelineService

# 一个带导航、面包屑、正文、页脚的仿真页
_PAGE = """
<html><body>
  <nav class="nav"><a href="/">首页</a><a href="/a">政采法规</a><a href="/b">信息公告</a></nav>
  <div class="breadcrumb">当前位置：首页 » 政采公告</div>
  <div class="vF_detail_main">
    <p>项目编号：2026-HF-001</p>
    <p>项目名称：合肥市充电桩建设项目（二期）</p>
    <p>采购方式：公开招标，预算金额 860 万元。</p>
    <p>投标人资格：具备电力工程施工总承包三级及以上资质，近三年类似业绩不少于两项。</p>
    <p>获取招标文件时间：2026年11月28日至12月5日，每日9:00至17:00。</p>
  </div>
  <footer class="footer">版权所有 中国政府采购网 ICP备000000号</footer>
  <div class="share">分享到：微信 微博</div>
</body></html>
"""


def test_selector_takes_main_block():
    text = extract_main_text(_PAGE, "div.vF_detail_main")
    assert "项目编号：2026-HF-001" in text
    assert "预算金额 860 万元" in text
    assert "版权所有" not in text  # 页脚被剔除
    assert "分享到" not in text


def test_known_class_without_selector():
    """没配选择器时，靠常见命名（content/detail 等）也能命中正文。"""
    html = '<html><body><div class="nav">菜单</div><div class="content"><p>' + "正文内容。" * 30 + "</p></div></body></html>"
    text = extract_main_text(html)
    assert "正文内容" in text
    assert "菜单" not in text


def test_density_floor_when_naming_is_generic():
    """命名不带线索时，用文本密度评分挑段落最密的块。"""
    html = (
        "<html><body>"
        '<div class="nav"><a href="/x">导航甲</a><a href="/y">导航乙</a></div>'
        "<div>" + "<p>" + "招标范围包括充电桩设备采购与安装调试。" * 6 + "</p>" + "</div>"
        "</body></html>"
    )
    text = extract_main_text(html)
    assert "招标范围" in text


def test_truncate_and_mark():
    text = extract_main_text(_PAGE, "div.vF_detail_main", max_chars=40)
    assert len(text) <= 40 + len("……（正文过长，已截断）")
    assert text.endswith("……（正文过长，已截断）")


def test_empty_html_returns_empty():
    assert extract_main_text("") == ""
    assert extract_main_text("   ") == ""


def test_noise_only_page_does_not_crash():
    html = '<html><body><script>var a=1</script><style>.x{}</style><nav><a href="/">首页</a></nav></body></html>'
    assert isinstance(extract_main_text(html), str)


# ------------------------------------------------------ 编排层：补抓与降级


def _site(code: str = "demo") -> SiteConfig:
    return SiteConfig(code=code, name="演示站", base_url="https://a.com", list_url="https://a.com/list?kw={keyword}")


def _notice(url: str = "https://a.com/1", content: str = "列表页只有标题摘要") -> Notice:
    return Notice(
        source_code="demo", source_name="演示站", title="标题", url=url, publish_date="", content=content
    )


def _service(crawler) -> PipelineService:
    svc = PipelineService.__new__(PipelineService)  # 只测这一个方法，绕过完整装配
    svc._config = AppConfig()
    svc._crawler = crawler
    svc._cleaner = RuleCleaner(AppConfig())
    return svc


def test_clean_block_keeps_paragraphs():
    """正文进 cleaner 后要保留分段，Word 里才不是一整块。"""
    out = RuleCleaner(AppConfig()).clean_block("第一段。\n第二段。\n\n第三段。")
    assert out.count("\n") == 2
    assert "第三段" in out


def test_no_detail_method_is_noop():
    """抓取器没实现 fetch_details（如 MockCrawlerClient）时整步跳过。"""

    class Bare:
        def fetch(self, site, intent):
            return []

    fresh = [_notice()]
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    _service(Bare())._enrich_details(fresh, [_site()], result)
    assert result.detail_fetched == 0
    assert result.warnings == []
    assert fresh[0].content == "列表页只有标题摘要"


def test_detail_fills_content_and_truncates():
    """补到正文后按 doc.max_chars_per_item 截断，口径与文档一致。"""

    class Fake:
        def fetch(self, site, intent):
            return []

        def fetch_details(self, site, notices, limit=None):
            notices[0].content = "招标范围包括设备采购与安装调试。" * 100  # 远超 600 字
            return [notices[0].url]

    fresh = [_notice()]
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    _service(Fake())._enrich_details(fresh, [_site()], result)
    assert result.detail_fetched == 1
    assert 600 <= len(fresh[0].content) <= 620
    assert fresh[0].content.startswith("招标范围")


def test_detail_failure_becomes_one_warning():
    """详情页整体失败只记一条汇总，不影响主链路。"""

    class Broken:
        def fetch(self, site, intent):
            return []

        def fetch_details(self, site, notices, limit=None):
            raise RuntimeError("连接被重置")

    fresh = [_notice(), _notice("https://a.com/2")]
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    _service(Broken())._enrich_details(fresh, [_site()], result)
    assert result.detail_fetched == 0
    # 两条：一条说原因，一条说影响（0/2），都不逐条刷屏
    assert len(result.warnings) == 2
    assert "详情页抓取失败" in result.warnings[0]
    assert "0/2" in result.warnings[1]


def test_partial_detail_reports_shortfall():
    class Partial:
        def fetch(self, site, intent):
            return []

        def fetch_details(self, site, notices, limit=None):
            notices[0].content = "正文" * 50
            return [notices[0].url]

    fresh = [_notice(), _notice("https://a.com/2")]
    result = PipelineResult(query="q", intent=ParsedIntent(raw_query="q"))
    _service(Partial())._enrich_details(fresh, [_site()], result)
    assert result.detail_fetched == 1
    assert any("1/2" in w for w in result.warnings)
