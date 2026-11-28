"""详情页正文抽取：选择器精确优先，通用启发式兜底。

为什么需要这一层：列表页只给标题＋几行摘要，"重点速览"经常只能看到两字标题，
Word 文档里也读不出招标范围、资格要求、预算这类决策信息。补抓详情页正文后，
摘要质量、关键词相关性判断、文档可读性同时改善——一处改动，三处受益。

三层降级：显式选择器 → 常见正文容器命名 → 文本密度评分 → body 兜底。
不引额外依赖（不用 readability/trafilatura），保持零新包，逻辑可审查、可单测。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

# 页面里注定不是正文的块，直接拆掉，省得参与评分
_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript")

# 只有块级标签才断行；span/a/b 这类内联标签不断，否则"名/称："会被拆成两行
_BREAK_TAGS = {
    "p", "div", "br", "tr", "li", "ul", "ol", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "dt", "dd", "td", "th", "hr",
}

# class/id 带这些词的基本是导航、页脚、分享、打印、面包屑、推荐位
_NOISE_ATTR_RE = re.compile(
    r"nav|menu|footer|header|copyright|breadcrumb|crumb|sidebar|share|print|banner|"
    r"advert|ads?|related|recommend|comment|login|search|toolbar|qrcode|weixin",
    re.I,
)

# 常见正文容器命名（政府站点与资讯站高频）
_MAIN_ATTR_RE = re.compile(r"content|article|artical|detail|mainbody|main-body|zoom|con-?t", re.I)

_MIN_TEXT = 60  # 低于这个字数不认为抽到了正文
_BLOCK_TAGS = ("div", "article", "section", "td", "body")


def extract_main_text(html: str, selector: str = "", max_chars: int = 2000) -> str:
    """从详情页 HTML 里取出正文纯文本。取不到返回空串（调用方保留列表页摘要）。"""
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    _drop_noise_by_attr(soup)
    _flatten_tables(soup)

    text = ""
    if selector:
        node = soup.select_one(selector)
        if node is not None:
            text = _node_text(node)
            if len(text.strip()) < _MIN_TEXT:
                # 选择器写错或页面改版时，别把几十字的空壳当正文，退回通用抽取
                text = ""

    if not text:
        text = _by_known_name(soup)
    if not text:
        text = _by_density(soup)
    if not text:
        text = _node_text(soup.body or soup)

    return _tidy(text, max_chars)


# ------------------------------------------------------ 三层候选


def _by_known_name(soup: BeautifulSoup) -> str:
    """按常见 class/id 命名挑正文容器：命中里取文本最长的那个。"""
    best = ""
    for node in soup.find_all(_BLOCK_TAGS):
        attrs = " ".join(_attr_tokens(node))
        if not _MAIN_ATTR_RE.search(attrs):
            continue
        if _NOISE_ATTR_RE.search(attrs):  # content 里夹着 toolbar 之类，跳过
            continue
        text = _node_text(node)
        if len(text) > len(best):
            best = text
    return best if len(best) >= _MIN_TEXT else ""


def _by_density(soup: BeautifulSoup) -> str:
    """文本密度评分：正文通常"字多、链接少、段落多"。

    score = 纯文本长度 - 2×链接文字长度 + 20×段落数
    链接惩罚防止把整页导航容器选成正文；段落加权让真正的正文块胜出。
    """
    best_score = 0
    best_text = ""
    for node in soup.find_all(_BLOCK_TAGS):
        text = _node_text(node)
        if len(text) < _MIN_TEXT:
            continue
        link_len = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
        p_count = len(node.find_all("p"))
        score = len(text) - 2 * link_len + 20 * p_count
        if score > best_score:
            best_score, best_text = score, text
    return best_text


def _node_text(node) -> str:
    """取节点纯文本，只在块级标签处断行（内联标签连写，避免把词拆碎）。"""
    parts: list[str] = []
    for el in node.descendants:
        if isinstance(el, NavigableString):
            parts.append(str(el))
        elif getattr(el, "name", None) in _BREAK_TAGS:
            parts.append("\n")
    return "".join(parts)


# ------------------------------------------------------ 清洗与截断


def _attr_tokens(node) -> list[str]:
    tokens: list[str] = []
    cls = node.get("class") or []
    if isinstance(cls, str):
        cls = [cls]
    tokens.extend(cls)
    node_id = node.get("id") or ""
    if node_id:
        tokens.append(node_id)
    return [str(t) for t in tokens if t]


def _drop_noise_by_attr(soup: BeautifulSoup) -> None:
    doomed = []
    for node in soup.find_all(True):
        attrs = " ".join(_attr_tokens(node))
        if attrs and _NOISE_ATTR_RE.search(attrs):
            doomed.append(node)
    for node in doomed:
        node.decompose()


def _flatten_tables(soup: BeautifulSoup) -> None:
    """把表格压成"一行一条记录"的文本。

    政府站点详情页大量用 table 排版，get_text("\\n") 会把每个单元格拆成单独一行，
    正文变成"采购单位 / 安徽某学校 / 地 / 址： / 合肥…"这种碎片，600 字额度全被
    地址电话吃光。按 tr 聚合单元格后，一行就是"字段 值"，可读性和信息密度都回来。
    """
    for table in soup.find_all("table"):
        rows: list[str] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            line = "  ".join(c for c in cells if c)
            if line:
                rows.append(line)
        if rows:
            table.replace_with(soup.new_string("\n" + "\n".join(rows) + "\n"))


_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
# 纯标点/分隔符行，也没有信息
_JUNK_LINE_RE = re.compile(r"^[\W_]+$", re.U)
# 纯英文标识符行（如页面锚点残留的 contentTable、vF_detail_content_container）
_ASCII_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# Word 列表域代码残渣：网页源码里的 <!--[if !supportLists]-->1.<!--[endif]-->
_MERGE_FIELD_RE = re.compile(r"\[if [^\]]*\]|\[endif\]", re.I)


def _tidy(text: str, max_chars: int) -> str:
    """按行收敛：压缩空白、丢纯符号行、合并重复空行，最后按字数截断。"""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = _MERGE_FIELD_RE.sub("", raw)
        line = _WS_RE.sub(" ", line).strip()
        if not line or _JUNK_LINE_RE.match(line) or _ASCII_ID_RE.match(line):
            continue
        lines.append(line)

    merged = "\n".join(lines)
    merged = re.sub(r"\n{3,}", "\n\n", merged).strip()
    if len(merged) > max_chars:
        merged = merged[:max_chars].rstrip("，。、；:：") + "……（正文过长，已截断）"
    return merged
