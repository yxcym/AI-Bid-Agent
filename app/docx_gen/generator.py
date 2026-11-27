"""Word 生成：python-docx，严格按 {查询}_{时间}.docx 命名。

字体按中文/英文分开设置（中文宋体、英文 Times New Roman），
这是正式交付文档的基本要求，不然中文在 Word 里会掉成等线体。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.config import AppConfig
from app.models import Notice, ParsedIntent

_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')
_LINK_COLOR = RGBColor(0x1F, 0x4E, 0x79)
_GREY = RGBColor(0x59, 0x59, 0x59)


class WordDocGenerator:
    def __init__(self, config: AppConfig) -> None:
        self._cfg = config
        self._out_dir = config.output_path()

    def generate(
        self, intent: ParsedIntent, notices: list[Notice], summary: dict | None = None
    ) -> Path:
        self._out_dir.mkdir(parents=True, exist_ok=True)
        doc = Document()
        self._setup_normal_style(doc)

        self._add_title(doc, intent.raw_query)
        self._add_meta(doc, intent, notices)
        if summary:
            self._add_summary(doc, summary)

        for i, notice in enumerate(notices, start=1):
            self._add_item(doc, i, notice)

        filename = f"{_safe_name(intent.raw_query)}_{datetime.now():%Y%m%d%H%M}.docx"
        target = self._out_dir / filename
        doc.save(str(target))
        return target

    # ------------------------------------------------------ 版面

    def _setup_normal_style(self, doc: Document) -> None:
        style = doc.styles["Normal"]
        style.font.name = self._cfg.doc.font_en
        style.font.size = Pt(10.5)
        rPr = style.element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:ascii"), self._cfg.doc.font_en)
        rFonts.set(qn("w:hAnsi"), self._cfg.doc.font_en)
        rFonts.set(qn("w:eastAsia"), self._cfg.doc.font_cn)
        style.paragraph_format.line_spacing = 1.5

    def _add_title(self, doc: Document, text: str) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run("招投标信息汇总")
        self._style_run(run, size=18, bold=True)
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run2 = p2.add_run(text)
        self._style_run(run2, size=11, color=_GREY)

    def _add_meta(self, doc: Document, intent: ParsedIntent, notices: list[Notice]) -> None:
        lines = [
            f"生成时间：{datetime.now():%Y-%m-%d %H:%M}",
            f"检索条件：关键词「{intent.keyword or '—'}」"
            f" / 区域「{intent.region or '不限'}」"
            f" / 时间「{intent.time_range_text or '—'}」",
            f"来源站点：{'、'.join(sorted({n.source_name for n in notices})) or '—'}",
            f"本轮新增：{len(notices)} 条",
        ]
        for line in lines:
            p = doc.add_paragraph()
            run = p.add_run(line)
            self._style_run(run, size=9, color=_GREY)
            p.paragraph_format.space_after = Pt(2)

        self._add_divider(doc)

    def _add_summary(self, doc: Document, summary: dict) -> None:
        """重点速览页：模型只给judgement，标题/链接由代码回填（防编造）。"""
        head = doc.add_paragraph()
        head_run = head.add_run("重点速览")
        self._style_run(head_run, size=13, bold=True)
        head.paragraph_format.space_before = Pt(4)
        head.paragraph_format.space_after = Pt(4)

        if summary.get("overview"):
            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Pt(21)
            run = p.add_run(summary["overview"])
            self._style_run(run, size=10.5)

        for pick in summary.get("picks") or []:
            title_p = doc.add_paragraph()
            title_p.paragraph_format.space_before = Pt(8)
            title_p.paragraph_format.space_after = Pt(2)
            title_run = title_p.add_run(f"{pick.get('index')}. {pick.get('title', '')}")
            self._style_run(title_run, size=11, bold=True)

            why_p = doc.add_paragraph()
            why_run = why_p.add_run(f"为什么值得看：{pick.get('why') or '—'}")
            self._style_run(why_run, size=10.5)

            risk_p = doc.add_paragraph()
            risk_run = risk_p.add_run(f"风险提示：{pick.get('risk') or '未见明显风险'}")
            self._style_run(risk_run, size=10.5, color=_GREY)

        note = doc.add_paragraph()
        note_run = note.add_run(
            f"本页由模型（{summary.get('model', '—')}）基于标题与正文节选生成，仅供参考；"
            "事实与原文以下方逐条列表为准。"
        )
        self._style_run(note_run, size=8.5, color=_GREY)
        note.paragraph_format.space_before = Pt(8)
        self._add_divider(doc)

    def _add_item(self, doc: Document, index: int, notice: Notice) -> None:
        p = doc.add_paragraph()
        run = p.add_run(f"{index}. {notice.title}")
        self._style_run(run, size=12, bold=True)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)

        meta = doc.add_paragraph()
        meta_run = meta.add_run(
            f"来源：{notice.source_name}　发布日期：{notice.publish_date or '未标注'}"
        )
        self._style_run(meta_run, size=9, color=_GREY)
        meta.paragraph_format.space_after = Pt(3)

        body = doc.add_paragraph()
        body.paragraph_format.first_line_indent = Pt(21)
        body_run = body.add_run(notice.content)
        self._style_run(body_run, size=10.5)

        link = doc.add_paragraph()
        link_run = link.add_run(f"原文链接：{notice.url}")
        self._style_run(link_run, size=9, color=_LINK_COLOR)

    def _add_divider(self, doc: Document) -> None:
        p = doc.add_paragraph()
        pPr = p._p.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:color"), "BFBFBF")
        borders.append(bottom)
        pPr.append(borders)

    # ------------------------------------------------------ 工具

    def _style_run(
        self,
        run,
        size: float | None = None,
        bold: bool = False,
        color: RGBColor | None = None,
    ) -> None:
        run.font.name = self._cfg.doc.font_en
        if size:
            run.font.size = Pt(size)
        run.font.bold = bold
        if color:
            run.font.color.rgb = color
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.append(rFonts)
        rFonts.set(qn("w:ascii"), self._cfg.doc.font_en)
        rFonts.set(qn("w:hAnsi"), self._cfg.doc.font_en)
        rFonts.set(qn("w:eastAsia"), self._cfg.doc.font_cn)


def _safe_name(text: str, limit: int = 40) -> str:
    cleaned = _ILLEGAL_FILENAME.sub("_", (text or "bid-agent").strip())
    return cleaned[:limit] or "bid-agent"
