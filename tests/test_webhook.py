"""群机器人推送单测：签名向量、payload 结构、开关语义、失败不外溢。

签名向量是硬编码的，一旦有人「顺手优化」加签实现（把飞书的 key/msg 位置写成钉钉那套），
这几条会立刻红。两家算法恰好互为镜像，是本次接入最大的坑。
"""

from __future__ import annotations

import httpx
import pytest

from app.config import AppConfig, PushSection, WebhookChannelSection
from app.models import Notice, ParsedIntent
from app.push.webhook import (
    WebhookChannel,
    WebhookPushError,
    WebhookPusher,
    dingtalk_sign,
    feishu_sign,
    merge_query,
)

SECRET = "test-secret"

# ---------------------------------------------------------------- 签名向量

def test_feishu_sign_vector() -> None:
    # key = "1700000000\ntest-secret"，msg = 空
    assert feishu_sign("1700000000", SECRET) == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="


def test_dingtalk_sign_vector() -> None:
    # key = secret，msg = "1700000000000\ntest-secret"；返回原始 base64，URL 编码由 merge_query 统一做
    assert dingtalk_sign("1700000000000", SECRET) == "BYMqUCZnSqbfPf1GCfZftO7Rg2g6P+Rp3/4+bLNtSGA="


def test_two_signs_are_not_interchangeable() -> None:
    """同一时间戳与密钥，两家结果必须不同；写成一样就是接错了。"""
    assert feishu_sign("1700000000", SECRET) != dingtalk_sign("1700000000", SECRET)


def test_feishu_sign_is_plain_base64() -> None:
    """飞书 sign 直接进 JSON，不能做 URL 编码（出现 % 就是错）。"""
    assert "%" not in feishu_sign("1700000000", SECRET)


def test_dingtalk_sign_is_url_encoded_once_in_url() -> None:
    """签名进 URL 时只编码一次：+ / = 变 %2B %2F %3D，且不得出现二次编码的 %25。"""
    sign = dingtalk_sign("1700000000000", SECRET)
    url = merge_query("https://oapi.dingtalk.com/robot/send?access_token=T", {"sign": sign})
    assert "%2B" in url and "%2F" in url and "%3D" in url
    assert "%25" not in url


# ---------------------------------------------------------------- query 合并（httpx params 会顶掉 URL 原有 query，实测踩过的坑）


def test_merge_query_keeps_access_token() -> None:
    url = merge_query(
        "https://oapi.dingtalk.com/robot/send?access_token=ABC123",
        {"timestamp": "1700000000000", "sign": "x+y/z="},
    )
    assert "access_token=ABC123" in url
    assert "timestamp=1700000000000" in url
    assert "sign=x%2By%2Fz%3D" in url  # 单次编码
    assert "%25" not in url


def test_merge_query_empty_is_noop() -> None:
    assert merge_query("https://f.invalid/hook/x", {}) == "https://f.invalid/hook/x"


def test_send_posts_to_merged_url(monkeypatch) -> None:
    """回归：send() 发出的 URL 必须同时带 URL 原有的 access_token 与新拼的签名参数。"""
    captured: dict = {}

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

        def post(self, url, json=None, params=None):
            captured["url"] = url
            return _FakeResp({"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr("app.push.webhook.httpx.Client", _Client)
    ch = _channel("dingtalk", secret=SECRET, webhook_url="https://oapi.dingtalk.com/robot/send?access_token=TOKEN123")
    body, url, query = ch.build_payload(_intent(), _fresh())
    ch.send(body, url, query)
    assert "access_token=TOKEN123" in captured["url"]
    assert "timestamp=" in captured["url"] and "sign=" in captured["url"]
    assert "%25" not in captured["url"]


# ---------------------------------------------------------------- payload 结构

def _channel(kind: str, **kw) -> WebhookChannel:
    cfg = WebhookChannelSection(
        enabled=kw.pop("enabled", True),
        webhook_url=kw.pop("webhook_url", "https://example.invalid/hook/x"),
        **kw,
    )
    return WebhookChannel(kind, cfg)


def _intent() -> ParsedIntent:
    return ParsedIntent(raw_query="最近3个月的合肥充电桩招标信息", keyword="充电桩", region="合肥")


def _fresh(n: int = 2) -> list[Notice]:
    return [
        Notice(
            source_code="hefei_gc",
            source_name="合肥公共资源交易中心·工程建设",
            title=f"某某充电桩设备采购项目第{i}标段",
            url=f"https://ggzy.hefei.gov.cn/jyxx/002001/x/{i}.html",
            publish_date="2026-11-27",
            content="正文略",
        )
        for i in range(1, n + 1)
    ]


def test_feishu_payload_uses_post_and_carries_text() -> None:
    body, url, query = _channel("feishu", secret="").build_payload(_intent(), _fresh())
    assert body["msg_type"] == "post"
    post = body["content"]["post"]["zh_cn"]
    assert post["title"].startswith("AI-Bid-Agent 推送")
    lines = post["content"][0]
    assert lines[0]["tag"] == "text"
    assert "充电桩" in lines[0]["text"]
    # 无 secret 时不得出现签名字段
    assert "timestamp" not in body and "sign" not in body
    assert query == {}


def test_feishu_payload_signs_when_secret_set() -> None:
    body, _, _ = _channel("feishu", secret=SECRET).build_payload(_intent(), _fresh())
    assert body["timestamp"].isdigit()
    assert len(body["timestamp"]) == 10  # 秒级
    assert body["sign"] == feishu_sign(body["timestamp"], SECRET)


def test_dingtalk_payload_is_markdown_with_sign_in_query() -> None:
    body, url, query = _channel("dingtalk", secret=SECRET).build_payload(_intent(), _fresh())
    assert body["msgtype"] == "markdown"
    assert body["markdown"]["title"].startswith("AI-Bid-Agent 推送")
    assert "###" in body["markdown"]["text"]
    assert "充电桩" in body["markdown"]["text"]
    assert len(query["timestamp"]) == 13  # 毫秒级
    assert query["sign"] == dingtalk_sign(query["timestamp"], SECRET)
    # 钉钉签名只在 query，不进 body
    assert "sign" not in body


def test_digest_truncates_to_max_items() -> None:
    body, _, _ = _channel("feishu", max_items_in_digest=1).build_payload(_intent(), _fresh(3))
    text = body["content"]["post"]["zh_cn"]["content"][0][0]["text"]
    assert "另有 2 条" in text


def test_test_payload_mentions_self_check() -> None:
    body, _, _ = _channel("feishu", secret="").build_test_payload()
    assert "自测" in body["content"]["text"]


# ---------------------------------------------------------------- 开关与状态

def _config(feishu: dict | None = None, dingtalk: dict | None = None) -> AppConfig:
    push = PushSection()
    if feishu:
        push.feishu = WebhookChannelSection(**feishu)
    if dingtalk:
        push.dingtalk = WebhookChannelSection(**dingtalk)
    return AppConfig(push=push)


def test_empty_url_means_not_ready() -> None:
    """enabled=true 但没填地址 = 未配置，不能算就绪，也不能尝试发。"""
    ch = WebhookChannel("feishu", WebhookChannelSection(enabled=True, webhook_url=""))
    assert ch.status()["ready"] is False
    assert ch.enabled is False


def test_pusher_reports_both_channels_always() -> None:
    p = WebhookPusher(_config())
    kinds = [s["kind"] for s in p.status()]
    assert kinds == ["feishu", "dingtalk"]
    assert p.enabled is False


def test_pusher_enabled_when_any_channel_ready() -> None:
    p = WebhookPusher(_config(dingtalk={"enabled": True, "webhook_url": "https://example.invalid/x"}))
    assert p.enabled is True
    assert [c.label for c in p.active] == ["钉钉群"]


def test_send_digest_skips_disabled_channel(monkeypatch) -> None:
    calls: list[str] = []

    def fake_send(self, body, url, query):
        calls.append(self.kind)

    monkeypatch.setattr(WebhookChannel, "send", fake_send)
    p = WebhookPusher(_config(feishu={"enabled": True, "webhook_url": "https://f.invalid/x"}))
    assert p.send_digest(_intent(), _fresh()) == "飞书群"
    assert calls == ["feishu"]


# ---------------------------------------------------------------- 失败语义

class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", "http://x"), response=self)

    def json(self) -> dict:
        return self._payload


def _patch_client(monkeypatch, resp: _FakeResp) -> None:
    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

        def post(self, url, json=None, params=None):
            return resp

    monkeypatch.setattr("app.push.webhook.httpx.Client", _Client)


def test_send_accepts_code_zero(monkeypatch) -> None:
    _patch_client(monkeypatch, _FakeResp({"code": 0, "msg": "success"}))
    _channel("feishu").send({}, "https://f.invalid/x", {})


def test_send_accepts_dingtalk_errcode_zero(monkeypatch) -> None:
    _patch_client(monkeypatch, _FakeResp({"errcode": 0, "errmsg": "ok"}))
    _channel("dingtalk").send({}, "https://d.invalid/x", {})


def test_send_rejects_business_error_with_hint(monkeypatch) -> None:
    # 飞书签名校验失败的经典业务码 19021
    _patch_client(monkeypatch, _FakeResp({"code": 19021, "msg": "sign match fail"}))
    with pytest.raises(WebhookPushError) as ei:
        _channel("feishu").send({}, "https://f.invalid/x", {})
    assert "19021" in str(ei.value)
    assert "签名" in str(ei.value)


def test_send_wraps_http_error(monkeypatch) -> None:
    _patch_client(monkeypatch, _FakeResp({}, status_code=404))
    with pytest.raises(WebhookPushError) as ei:
        _channel("dingtalk").send({}, "https://d.invalid/x", {})
    assert "404" in str(ei.value)


def test_send_without_url_raises() -> None:
    ch = WebhookChannel("feishu", WebhookChannelSection(enabled=True, webhook_url=""))
    with pytest.raises(WebhookPushError):
        ch.send({}, "", {})
