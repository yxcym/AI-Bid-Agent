"""清洗模块：把网页正文变成“能进决策文档”的事实。

三件事：
  1. 去掉 HTML 残渣与广告模板行
  2. 空白归一、超长截断
  3. 算指纹（标题 + 归一化 URL），供去重使用
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from app.config import AppConfig
from app.models import Notice, RawNotice

_AD_PATTERNS = (
    r"【?广告】?",
    r"推广服务",
    r"友情链接",
    r"版权所有",
    r"Copyright",
    r"ICP\s*备",
    r"免责声明",
    r"扫码关注",
    r"点击下载\s*APP",
)

_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
_BLOCK_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class RuleCleaner:
    def __init__(self, config: AppConfig) -> None:
        self._max_chars = config.doc.max_chars_per_item
        self._ad_re = re.compile("|".join(f"({p})" for p in _AD_PATTERNS), re.I)

    def clean(self, raw: RawNotice) -> Notice:
        content = self._clean_text(raw.content)
        return Notice(
            source_code=raw.source_code,
            source_name=raw.source_name,
            title=self._clean_text(raw.title, limit=120).rstrip("。，；、"),
            url=raw.url.strip(),
            publish_date=(raw.publish_date or "").strip(),
            content=content,
            fingerprint=fingerprint(raw.title, raw.url),
        )

    def is_relevant(self, notice: Notice, keyword: str) -> bool:
        if not keyword:
            return True
        return keyword in notice.title or keyword in notice.content

    # ------------------------------------------------------ 内部

    def _clean_text(self, text: str, limit: int | None = None) -> str:
        if not text:
            return ""
        text = _BLOCK_RE.sub("", text)
        text = _TAG_RE.sub(" ", text)
        text = _WS_RE.sub(" ", text)

        # 逐行剔广告：整行命中任一广告模式就整行丢
        lines = [ln.strip() for ln in re.split(r"[\r\n|]+", text) if ln.strip()]
        lines = [ln for ln in lines if not self._ad_re.search(ln)]
        # 单行里夹着广告尾巴时，按句切掉那一句
        kept = []
        for ln in lines:
            for seg in re.split(r"。", ln):
                seg = seg.strip()
                if not seg or self._ad_re.search(seg):
                    continue
                kept.append(seg + "。")
        text = "".join(kept) if kept else " ".join(lines)

        text = _WS_RE.sub(" ", text).strip()
        cap = limit or self._max_chars
        if len(text) > cap:
            text = text[:cap].rstrip("，。、；") + "……（原文过长，已截断）"
        return text


def fingerprint(title: str, url: str) -> str:
    """标题 + 去掉查询参数与锚点的 URL，哈希成 16 位。

    查询参数里常有 utm_source、时间戳、随机 id，不归一化会导致同一篇文章
    每次抓都算“新”的，去重直接失效。
    """
    parts = urlsplit(url.strip())
    normalized_url = f"{parts.scheme}://{parts.netloc}{parts.path}".lower()
    seed = f"{(title or '').strip()}|{normalized_url}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]
