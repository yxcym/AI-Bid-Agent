"""群机器人推送：飞书 / 钉钉自定义机器人，同文本内容的两个协议适配。

为什么两个都做：用户钉钉有现成群可自测，飞书是本项目生态主场（2026 飞书 AI 大赛）。
两者协议同构但字段名与加签算法不同，坑集中在签名：

  飞书：sign = base64(HMAC-SHA256(key="{ts}\\n{secret}", msg=""))，ts 为秒级，sign 放 body
  钉钉：sign = urlencode(base64(HMAC-SHA256(key=secret, msg="{ts}\\n{secret}")))，ts 为毫秒，sign 放 URL query

注意两者 key 与 msg 的位置正好互换——把钉钉的写法照搬到飞书必然签名不匹配。
本模块有回归测试锁死这两条向量。

构造与发送分离（_build_* / _post）：不连网也能自测 payload 结构。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from datetime import datetime

import httpx

from app.config import WebhookChannelSection
from app.models import Notice, ParsedIntent

log = logging.getLogger(__name__)


class WebhookPushError(Exception):
    """推送配置或发送失败。消息面向用户，直接进 warnings / 报错弹层。"""


# ---------------------------------------------------------------- 加签（纯函数，可单测）


def feishu_sign(timestamp: str, secret: str) -> str:
    """飞书加签：以「时间戳\\n密钥」为 HMAC 的 key，空消息。"""
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def dingtalk_sign(timestamp: str, secret: str) -> str:
    """钉钉加签：以密钥为 HMAC 的 key，「时间戳\\n密钥」为消息，结果需 URL 编码。"""
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))


# ---------------------------------------------------------------- 单个通道


class WebhookChannel:
    """一个群机器人通道。kind 决定协议，其余配置通用。"""

    def __init__(self, kind: str, cfg: WebhookChannelSection) -> None:
        self.kind = kind  # feishu | dingtalk
        self._cfg = cfg

    @property
    def label(self) -> str:
        return "飞书群" if self.kind == "feishu" else "钉钉群"

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled and self._cfg.webhook_url.strip())

    def status(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "enabled": self._cfg.enabled,
            "ready": self.enabled,
            "signed": bool(self._cfg.secret.strip()),
        }

    # ------------------------------------------------------ 消息构建（无网络副作用）

    def _digest_text(self, intent: ParsedIntent, fresh: list[Notice], summary: dict | None = None) -> str:
        c = self._cfg
        lines = []
        if summary:
            lines.append("【重点速览·模型摘要】")
            if summary.get("overview"):
                lines.append(summary["overview"])
            for pick in summary.get("picks") or []:
                # 群消息是扫一眼场景：理由截断，完整判断在 Word 里
                why = (pick.get("why") or "—")[:60]
                lines.append(f"· {pick.get('title')}｜{why}")
            lines.append("")
        lines += [
            f"检索原话：{intent.raw_query}",
            f"关键词：{intent.keyword or '—'}　区域：{intent.region or '不限'}　时间：{intent.time_range_text or '不限'}",
            f"本轮新增：{len(fresh)} 条　生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        for i, n in enumerate(fresh[: c.max_items_in_digest], 1):
            lines.append(f"{i}. {n.title}")
            lines.append(f"   {n.publish_date or '—'} · {n.source_name}")
            lines.append(f"   {n.url}")
        if len(fresh) > c.max_items_in_digest:
            lines.append(f"…… 另有 {len(fresh) - c.max_items_in_digest} 条，完整明细见推送的 Word 文档")
        lines.append("")
        lines.append("—— AI-Bid-Agent 自动推送（增量语义：本轮无新增则不会有这条消息）")
        return "\n".join(lines)

    def build_payload(
        self, intent: ParsedIntent, fresh: list[Notice], summary: dict | None = None
    ) -> tuple[dict, str, dict]:
        """返回 (body, url, query)；query 只对钉钉加签有用。"""
        title = f"AI-Bid-Agent 推送：{intent.keyword or '招标信息'} {len(fresh)} 条新增"
        c = self._cfg
        body_text = self._digest_text(intent, fresh, summary)
        if self.kind == "feishu":
            body: dict = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title,
                            "content": [[{"tag": "text", "text": body_text}]],
                        }
                    }
                },
            }
            if c.secret.strip():
                ts = str(int(time.time()))
                body["timestamp"] = ts
                body["sign"] = feishu_sign(ts, c.secret.strip())
            return body, c.webhook_url.strip(), {}

        # 钉钉 markdown：标题 + 正文；加签参数拼 query
        body = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"### {title}\n\n{body_text}"},
        }
        query: dict = {}
        if c.secret.strip():
            ts = str(int(time.time() * 1000))
            query = {"timestamp": ts, "sign": dingtalk_sign(ts, c.secret.strip())}
        return body, c.webhook_url.strip(), query

    def build_test_payload(self) -> tuple[dict, str, dict]:
        intent = ParsedIntent(raw_query="群机器人连通性测试")
        title = f"AI-Bid-Agent 推送通道自测（{self.label}）"
        if self.kind == "feishu":
            body: dict = {"msg_type": "text", "content": {"text": f"{title}\n配置已生效，收到本条即说明 webhook 与加签都正确。"}}
            if self._cfg.secret.strip():
                ts = str(int(time.time()))
                body["timestamp"] = ts
                body["sign"] = feishu_sign(ts, self._cfg.secret.strip())
            return body, self._cfg.webhook_url.strip(), {}
        body = {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n配置已生效，收到本条即说明 webhook 与加签都正确。"}}
        query: dict = {}
        if self._cfg.secret.strip():
            ts = str(int(time.time() * 1000))
            query = {"timestamp": ts, "sign": dingtalk_sign(ts, self._cfg.secret.strip())}
        return body, self._cfg.webhook_url.strip(), query

    # ------------------------------------------------------ 发送

    def send(self, body: dict, url: str, query: dict) -> None:
        if not url:
            raise WebhookPushError(f"{self.label} 未配置 webhook 地址")
        try:
            with httpx.Client(timeout=self._cfg.timeout_seconds) as client:
                resp = client.post(url, json=body, params=query)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise WebhookPushError(
                f"{self.label} 推送失败：HTTP {exc.response.status_code}。"
                "常见原因：webhook 地址失效、关键词/加签/IP 白名单校验不通过"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WebhookPushError(f"{self.label} 推送失败：{type(exc).__name__}: {exc}") from exc

        # 两家都用「业务码 0 = 成功」，字段名不同
        code = data.get("code", data.get("errcode", data.get("StatusCode", 0)))
        if code not in (0, None):
            msg = data.get("msg") or data.get("errmsg") or "未知错误"
            detail = ""
            if code in (19021, 310000):
                detail = "（多为签名/时间戳校验失败，检查加签密钥并确认机器时间准确）"
            raise WebhookPushError(f"{self.label} 推送被拒：code={code} msg={msg}{detail}")
        log.info("%s 推送成功", self.label)


# ---------------------------------------------------------------- 聚合入口


class WebhookPusher:
    """按配置装配 0-2 个通道。enabled 为真才参与流水线推送。"""

    def __init__(self, config) -> None:
        self.channels = [
            WebhookChannel("feishu", config.push.feishu),
            WebhookChannel("dingtalk", config.push.dingtalk),
        ]

    @property
    def active(self) -> list[WebhookChannel]:
        return [c for c in self.channels if c.enabled]

    @property
    def enabled(self) -> bool:
        return bool(self.active)

    def status(self) -> list[dict]:
        return [c.status() for c in self.channels]

    def send_digest(
        self, intent: ParsedIntent, fresh: list[Notice], doc_path=None, summary: dict | None = None
    ) -> str:
        # doc_path 只为与 EmailPusher 保持同一鸭子类型签名；群机器人不支持附件，文档另走下载链接
        sent: list[str] = []
        for c in self.active:
            body, url, query = c.build_payload(intent, fresh, summary)
            c.send(body, url, query)
            sent.append(c.label)
        return "、".join(sent)

    def send_test(self) -> str:
        sent: list[str] = []
        for c in self.active:
            body, url, query = c.build_test_payload()
            c.send(body, url, query)
            sent.append(c.label)
        return "、".join(sent)
