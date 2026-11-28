"""规则解析器：不接大模型也能把一句话拆成结构化意图。

为什么先写规则：模型一上就要钱、要 key、要处理幻觉，
而“最近3个月 / 上海 / 充电桩 / 每天9:00”这种表达用正则就能拿到 80% 的准确率。
等 LLM 解析器接上以后，这个类继续当兜底（模型失败时回退到这里）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.config import SiteConfig
from app.models import ParsedIntent

NAME = "rule"

# ---------------------------------------------------------------- 词典

# 省级行政区 + 常见地级市。命中即视为“区域”，够覆盖赛题演示场景。
REGIONS: tuple[str, ...] = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
    "合肥", "芜湖", "蚌埠", "安庆", "南京", "苏州", "无锡", "常州", "南通",
    "徐州", "杭州", "宁波", "温州", "绍兴", "嘉兴", "金华", "武汉", "长沙",
    "南昌", "郑州", "洛阳", "济南", "青岛", "烟台", "太原", "西安", "成都",
    "绵阳", "昆明", "贵阳", "南宁", "桂林", "福州", "厦门", "泉州", "广州",
    "深圳", "珠海", "佛山", "东莞", "中山", "惠州", "沈阳", "大连", "长春",
    "哈尔滨", "石家庄", "唐山", "呼和浩特", "乌鲁木齐", "兰州", "西宁", "银川",
    "海口", "三亚", "拉萨",
)

# 关键词的锚点：这些词之前的那一段就是业务主题
ANCHORS: tuple[str, ...] = (
    "招标信息", "招标公告", "采购公告", "中标公告", "招投标", "招标", "采购", "公告",
)

# 意图噪声词，提取关键词前先剥掉
NOISE_WORDS: tuple[str, ...] = (
    "帮我找一下", "帮我找", "帮我看一下", "帮我看", "帮帮我", "我想找", "帮我查",
    "查一下", "找一下", "看一下", "看看", "看下", "查查", "帮我", "请帮我",
    "请汇总", "汇总", "发送给我", "发给我", "都有哪些", "有哪些", "有什么",
    "相关信息", "信息", "一下", "请",
)

_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_WEEKDAY_CRON = {"日": "0", "天": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6"}


# ---------------------------------------------------------------- 入口

class RuleIntentParser:
    name = NAME

    def parse(self, query: str, available_sites: list[SiteConfig]) -> ParsedIntent:
        text = _normalize(query)

        frequency_text, cron = _extract_frequency(text)
        if frequency_text:
            text = text.replace(frequency_text, " ")

        time_text, start_date, end_date = _extract_time(text)
        if time_text:
            text = text.replace(time_text, " ")

        region, region_phrase = _extract_region(text)
        if region_phrase:
            text = text.replace(region_phrase, " ")

        keyword = _extract_keyword(text)
        sites = _match_sites(query, available_sites)

        return ParsedIntent(
            raw_query=query,
            keyword=keyword,
            region=region,
            time_range_text=time_text or "最近90天（默认）",
            start_date=start_date,
            end_date=end_date,
            cron=cron,
            frequency_text=frequency_text,
            sites=sites,
            parser=self.name,
        )


# ---------------------------------------------------------------- 频率

def _extract_frequency(text: str) -> tuple[str | None, str | None]:
    """返回 (频率原文, 标准 cron)。cron 五段: 分 时 日 月 周（周日=0）。

    时段词含"早上/凌晨/清晨"；分钟支持"半"（8点半=8:30）。
    缺这两样时，"每天早上8点半"这类高频口语会静默不注册定时——比报错更坑。
    """
    periods = r"(上午|中午|下午|晚上|早上|清晨|凌晨)?"

    def _to_24h(period: str | None, hour: int) -> int:
        if period in ("下午", "晚上") and hour < 12:
            return hour + 12
        if period == "中午" and hour < 11:
            return hour + 12
        return hour  # 上午/早上/清晨/凌晨不偏移

    # 每天 9:00 / 每天早上8点半 / 每天上午9点30
    m = re.search(r"每(?:天|日)\s*" + periods + r"\s*(\d{1,2})\s*(?:[:：点]\s*(半|\d{1,2})?)?", text)
    if m:
        hour = _to_24h(m.group(1), int(m.group(2)))
        minute = 30 if m.group(3) == "半" else int(m.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return m.group(0).strip(), f"{minute} {hour} * * *"

    # 每周一 9:00 / 每周一早上8点半
    m = re.search(r"每(?:周|星期)\s*([一二三四五六日天])\s*" + periods + r"\s*(\d{1,2})\s*(?:[:：点]\s*(半|\d{1,2})?)?", text)
    if m:
        weekday = _WEEKDAY_CRON.get(m.group(1), "1")
        hour = _to_24h(m.group(2), int(m.group(3)))
        minute = 30 if m.group(4) == "半" else int(m.group(4) or 0)
        return m.group(0).strip(), f"{minute} {hour} * * {weekday}"

    # 每月 1 号 9:00
    m = re.search(r"每(?:个)?月\s*(\d{1,2})\s*[号日]\s*" + periods + r"\s*(\d{1,2})\s*(?:[:：点]\s*(半|\d{1,2})?)?", text)
    if m:
        day = int(m.group(1))
        hour = _to_24h(m.group(2), int(m.group(3)))
        minute = 30 if m.group(4) == "半" else int(m.group(4) or 0)
        return m.group(0).strip(), f"{minute} {hour} {day} * *"

    # 每小时
    if re.search(r"每(?:个)?小时", text):
        return "每小时", "0 * * * *"

    return None, None


# ---------------------------------------------------------------- 时间

def _extract_time(text: str) -> tuple[str | None, date, date]:
    """返回 (时间原文, 起始日, 截止日)。没有明确时间就给最近 90 天。"""

    today = date.today()

    m = re.search(r"(?:最近|近|过去)\s*(\d+|[一二三四五六七八九十]+)\s*个?\s*月", text)
    if m:
        n = _cn_to_int(m.group(1))
        return m.group(0).strip(), _shift_months(today, -n), today

    m = re.search(r"(?:最近|近|过去)\s*(\d+|[一二三四五六七八九十]+)\s*(?:天|日)", text)
    if m:
        n = _cn_to_int(m.group(1))
        return m.group(0).strip(), today - timedelta(days=n), today

    m = re.search(r"(?:最近|近|过去)\s*(\d+|[一二三四五六七八九十]+)\s*(?:周|个?星期)", text)
    if m:
        n = _cn_to_int(m.group(1))
        return m.group(0).strip(), today - timedelta(weeks=n), today

    if "上个月" in text:
        first_this_month = today.replace(day=1)
        last_prev_end = first_this_month - timedelta(days=1)
        return "上个月", last_prev_end.replace(day=1), last_prev_end

    if "本月" in text or "这个月" in text:
        return "本月", today.replace(day=1), today

    if "今天" in text or "今日" in text:
        return "今天", today, today

    if "本周" in text or "这周" in text:
        monday = today - timedelta(days=today.weekday())
        return "本周", monday, today

    return None, today - timedelta(days=90), today


# ---------------------------------------------------------------- 区域

def _extract_region(text: str) -> tuple[str | None, str | None]:
    for region in sorted(REGIONS, key=len, reverse=True):
        if region not in text:
            continue
        # 把“上海区域内 / 上海地区 / 上海市”整段吃掉，避免残字污染关键词
        m = re.search(re.escape(region) + r"(?:市|省|区域|地区|范围内)?", text)
        return region, (m.group(0) if m else region)
    return None, None


# ---------------------------------------------------------------- 关键词

# 主题词尾部的泛词：留着它们会让搜索变成"全网找含'建设'二字的公告"
_TAIL_GENERIC_RE = re.compile(
    r"(?:建设|工程|项目|采购|招标|投标|施工|安装|调试|服务|设备|货物|总承包|信息|公告|汇总|情况|相关"
    r"|最近|最新|近期|目前|现在|有关|关于)+$"
)


def _trim_generic(segment: str) -> str:
    """剥掉尾部的“的”和泛词；剥完不足两个字就返回空（说明这段本身没信息量）。"""
    trimmed = _TAIL_GENERIC_RE.sub("", segment.strip("的")).strip("的")
    return trimmed if len(trimmed) >= 2 else ""


def _extract_keyword(text: str) -> str:
    for noise in NOISE_WORDS:
        text = text.replace(noise, " ")

    # 截到锚点之前：锚点后面通常只是“都有哪些”“发给我”之类的壳
    head = text
    for anchor in ANCHORS:
        idx = text.find(anchor)
        if idx > 0:
            head = text[:idx]
            break

    # 只留中英文数字，切成片段
    parts = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", head)
    if not parts:
        return ""

    # 从后往前找“剥掉泛词后还有内容”的那一段：业务主题一般在最后，
    # 但最后一段常常是“建设/招标/采购”这种放之四海皆准的词
    picked = ""
    for seg in reversed(parts):
        candidate = _trim_generic(seg)
        if candidate:
            picked = candidate
            break

    if not picked:
        # 整句都是泛词（例如只说“最近的招标公告”），剥完仍没东西就返回空，
        # 让上层直接提示用户补主题，而不是拿去全网搜“公告”两个字
        fallback = _TAIL_GENERIC_RE.sub("", parts[-1].strip("的")).strip("的")
        picked = fallback if len(fallback) >= 2 else ""

    if not picked:
        return ""

    # 太长的通常是残留偏正结构，取尾部 8 个字
    return picked[-8:] if len(picked) > 8 else picked


def _match_sites(query: str, available_sites: list[SiteConfig]) -> list[str]:
    """用户点名了站点就只抓它，没点名就交给配置里的全部启用站点。"""
    hit: list[str] = []
    for site in available_sites:
        if site.name and site.name in query:
            hit.append(site.code)
            continue
        # 域名主体（去掉 www 和协议）也算点名
        host = re.sub(r"^https?://", "", site.base_url).split("/")[0].replace("www.", "")
        if host and host in query:
            hit.append(site.code)
    return hit


# ---------------------------------------------------------------- 工具

def _normalize(text: str) -> str:
    text = text.strip()
    # 全角标点归一，保留汉字
    table = str.maketrans("，。；：、（）【】“”‘’", ",,;:,()[]\"\"''")
    return re.sub(r"\s+", " ", text.translate(table))


def _cn_to_int(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    if raw in _CN_DIGITS:
        return _CN_DIGITS[raw]
    # 十一、十二 这类
    if raw.startswith("十"):
        return 10 + _CN_DIGITS.get(raw[1:], 0)
    return 1


def _shift_months(d: date, delta: int) -> date:
    month_index = d.year * 12 + (d.month - 1) + delta
    year, month = divmod(month_index, 12)
    month += 1
    # 处理 31 号减月越界
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)
