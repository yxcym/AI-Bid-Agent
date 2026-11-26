"""Mock 抓取：不联网也能把全链路跑通。

它的价值不是“假数据”，而是把三种真实世界的脏东西固定造出来：
  1. 广告/推广/版权噪声行
  2. 完全重复的条目（用来验证指纹去重真的生效）
  3. 摘要过长、空白混乱的正文（用来验证清洗与截断）
"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import AppConfig, SiteConfig
from app.models import ParsedIntent, RawNotice

_AD_LINES = (
    "【广告】本条由某某咨询有限公司提供推广服务，详情请访问官网。",
    "友情链接：中国建设招标网 | 全国公共资源交易平台 | 本站已通过 ISO 认证",
    "版权所有 © 2026 示例招标信息网 京 ICP 备 00000000 号",
)

_TEMPLATES = (
    "{region}{keyword}项目一期工程施工总承包招标公告",
    "{region}{keyword}设备采购及安装项目公开招标",
    "{keyword}运维服务采购项目竞争性磋商公告",
    "{region}{keyword}二期扩建工程监理服务招标",
)


class MockCrawlerClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def fetch(self, site: SiteConfig, intent: ParsedIntent) -> list[RawNotice]:
        if not site.enabled:
            return []

        region = intent.region or ""
        keyword = intent.keyword or "招标"
        today = date.today()

        notices: list[RawNotice] = []
        for i, tpl in enumerate(_TEMPLATES):
            title = tpl.format(region=region, keyword=keyword)
            publish = today - timedelta(days=i * 7 + 2)
            body = " ".join(
                [
                    f"项目名称：{title}。",
                    f"采购单位：{region or '某'}市公共资源交易中心。",
                    f"公告日期：{publish.isoformat()}。",
                    "预算金额：人民币 1,280.00 万元。",
                    "投标人资格要求：具备独立法人资格、相关行业资质二级及以上，近三年无重大违法记录。",
                    "获取招标文件时间：公告发布之日起 5 个工作日内。",
                    f"投标截止时间：{(publish + timedelta(days=20)).isoformat()} 09:30。",
                    _AD_LINES[i % len(_AD_LINES)],
                ]
            )
            notices.append(
                RawNotice(
                    source_code=site.code,
                    source_name=site.name,
                    title=title,
                    url=f"{site.base_url}/notice/{site.code}-{i + 1}",
                    publish_date=publish.isoformat(),
                    content=body,
                    need_login=site.needs_login,
                )
            )

        # 刻意制造一条跨站重复：同标题、同 URL 主体，验证去重不是摆设
        if notices:
            dup = RawNotice(
                source_code=site.code,
                source_name=site.name,
                title=notices[0].title,
                url=notices[0].url,
                publish_date=notices[0].publish_date,
                content=notices[0].content,
                need_login=site.needs_login,
            )
            notices.append(dup)

        return notices
