"""邮件推送：Word 附件 + 摘要正文，挂在「文档生成成功」这个点上。

语义对齐增量推送：无新增 → 不出文档 → 不发邮件，天然免打扰。

凭据安全：授权码只放 config/secrets.yaml（已 gitignore），
不进 config.yaml，更不进 git。SMTP 服务器没配时按发件邮箱域名自动推断。

构造与发送分离（_build_message / _send）：不连网也能自测消息结构。
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from app.config import AppConfig
from app.models import Notice, ParsedIntent

log = logging.getLogger(__name__)

# 常见服务商 SMTP 预设；没命中的域名要求显式配置 smtp_host/smtp_port
_SMTP_PRESETS = {
    "qq.com": ("smtp.qq.com", 465),
    "foxmail.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
    "sina.com": ("smtp.sina.com", 465),
    "gmail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_DOCX_MIME = (
    "application",
    "vnd.openxmlformats-officedocument.wordprocessingml.document",
)


class EmailPushError(Exception):
    """推送配置或发送失败。消息面向用户，直接进 warnings / 报错弹层。"""


class EmailPusher:
    label = "邮件"

    def __init__(self, config: AppConfig) -> None:
        self._cfg = config.push.email

    # ------------------------------------------------------ 状态

    @property
    def enabled(self) -> bool:
        """enabled=true 且凭据齐全才实际发信；缺哪样都在 status 里可见。"""
        c = self._cfg
        return bool(c.enabled and c.username and c.password and c.to_addrs)

    def status(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "ready": self.enabled,
            "username": self._cfg.username,
            "recipients": len(self._cfg.to_addrs),
        }

    # ------------------------------------------------------ 配置解析

    def _resolve_host_port(self) -> tuple[str, int]:
        c = self._cfg
        host, port = c.smtp_host.strip(), c.smtp_port
        if not host or not port:
            domain = c.username.rsplit("@", 1)[-1].strip().lower()
            preset_host, preset_port = _SMTP_PRESETS.get(domain, ("", 0))
            host = host or preset_host
            port = port or preset_port
        if not host or not port:
            raise EmailPushError(
                "SMTP 服务器未配置且无法按邮箱域名推断，"
                "请在 config.yaml 的 push.email 里填 smtp_host / smtp_port"
            )
        return host, port

    def _validate(self) -> None:
        c = self._cfg
        if not c.username or not _EMAIL_RE.match(c.username):
            raise EmailPushError(f"发件邮箱不合法：{c.username!r}")
        if not c.password:
            raise EmailPushError(
                "SMTP 授权码为空：请填入 config/secrets.yaml（该文件已 gitignore，不会入库）"
            )
        bad = [a for a in c.to_addrs if not _EMAIL_RE.match(a)]
        if bad:
            raise EmailPushError(f"收件邮箱不合法：{bad}")

    # ------------------------------------------------------ 消息构建（无网络副作用）

    def _digest_text(self, intent: ParsedIntent, fresh: list[Notice]) -> str:
        c = self._cfg
        lines = [
            "AI-Bid-Agent 招标信息推送",
            "",
            f"检索原话：{intent.raw_query}",
            f"关键词：{intent.keyword or '—'}　区域：{intent.region or '不限'}"
            f"　时间：{intent.time_range_text or '不限'}",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"本轮新增：{len(fresh)} 条（完整明细见附件 Word）",
            "",
            f"条目速览（前 {min(c.max_items_in_digest, len(fresh))} 条）：",
        ]
        for i, n in enumerate(fresh[: c.max_items_in_digest], 1):
            lines.append(f"{i}. {n.title}")
            lines.append(f"   发布：{n.publish_date or '—'}　来源：{n.source_name}")
            lines.append(f"   链接：{n.url}")
        lines.append("")
        lines.append("—— 由 AI-Bid-Agent 自动发送（增量语义：本轮无新增则不会有这封邮件）")
        return "\n".join(lines)

    def _digest_html(self, intent: ParsedIntent, fresh: list[Notice]) -> str:
        c = self._cfg
        items = []
        for n in fresh[: c.max_items_in_digest]:
            date = n.publish_date or "—"
            items.append(
                f"<li><a href=\"{n.url}\">{n.title}</a>"
                f"<br><small style=\"color:#64748b\">{date} · {n.source_name}</small></li>"
            )
        return (
            "<div style=\"font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "max-width:640px;margin:0 auto;color:#1c2733\">"
            "<h2 style=\"border-bottom:2px solid #1f4e79;padding-bottom:8px\">AI-Bid-Agent 招标信息推送</h2>"
            f"<p>检索原话：<b>{intent.raw_query}</b></p>"
            f"<p>关键词 <b>{intent.keyword or '—'}</b>　区域 <b>{intent.region or '不限'}</b>"
            f"　时间 <b>{intent.time_range_text or '不限'}</b>"
            f"　本轮新增 <b style=\"color:#1a7f37\">{len(fresh)}</b> 条（完整明细见附件 Word）</p>"
            f"<ol>{''.join(items)}</ol>"
            "<p style=\"color:#94a3b8;font-size:12px\">增量推送：本轮无新增时不会有这封邮件。</p>"
            "</div>"
        )

    def _build_message(
        self,
        subject: str,
        intent: ParsedIntent,
        fresh: list[Notice],
        doc_path: Path | None = None,
    ) -> EmailMessage:
        self._validate()
        c = self._cfg
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr(("AI-Bid-Agent", c.from_addr or c.username))
        msg["To"] = ", ".join(c.to_addrs)
        msg.set_content(self._digest_text(intent, fresh))
        msg.add_alternative(self._digest_html(intent, fresh), subtype="html")
        if doc_path is not None:
            if not doc_path.exists():
                raise EmailPushError(f"附件不存在：{doc_path}")
            msg.add_attachment(
                doc_path.read_bytes(),
                maintype=_DOCX_MIME[0],
                subtype=_DOCX_MIME[1],
                filename=doc_path.name,
            )
        return msg

    # ------------------------------------------------------ 发送

    def send_digest(self, intent: ParsedIntent, fresh: list[Notice], doc_path: Path) -> str:
        subject = f"AI-Bid-Agent 推送：{intent.keyword} 招标信息 {len(fresh)} 条新增"
        msg = self._build_message(subject, intent, fresh, doc_path)
        self._send(msg)
        return ", ".join(self._cfg.to_addrs)

    def send_test(self) -> str:
        msg = self._build_message("AI-Bid-Agent SMTP 配置测试", ParsedIntent(raw_query="SMTP 连通性测试"), [])
        self._send(msg)
        return ", ".join(self._cfg.to_addrs)

    def _send(self, msg: EmailMessage) -> None:
        host, port = self._resolve_host_port()
        c = self._cfg
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=c.timeout_seconds) as s:
                    s.login(c.username, c.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=c.timeout_seconds) as s:
                    s.starttls()
                    s.login(c.username, c.password)
                    s.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailPushError(
                "SMTP 认证失败：请确认填的是授权码而非登录密码，且邮箱已开启 SMTP 服务"
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailPushError(f"收件人被拒：{exc}") from exc
        except (smtplib.SMTPException, OSError, socket.timeout) as exc:
            raise EmailPushError(f"SMTP 发送失败：{type(exc).__name__}: {exc}") from exc
        log.info("邮件已发送 -> %s（%d 个附件）", c.to_addrs, 1 if msg.iter_attachments() else 0)
