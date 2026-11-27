"""凭据快照与漂移检查的单测：备份只在文件健康时刷新，误删当场告警且不污染备份。"""

from __future__ import annotations

import yaml

from app.config import backup_secrets, check_secrets_drift

_GOOD = {
    "push": {
        "email": {"username": "a@126.com", "password": "secret", "to_addrs": ["b@163.com"]},
        "dingtalk": {"enabled": True, "webhook_url": "https://oapi/x?access_token=t", "secret": "SEC"},
        "feishu": {"enabled": False, "webhook_url": ""},
    },
    "intent": {"llm": {"api_key": "sk-1"}},
}


def _write(path, data) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_backup_creates_snapshot(tmp_path) -> None:
    src, bak = tmp_path / "secrets.yaml", tmp_path / "secrets.yaml.bak"
    _write(src, _GOOD)
    assert backup_secrets(src, bak) is True
    assert yaml.safe_load(bak.read_text(encoding="utf-8")) == _GOOD


def test_backup_skips_missing_source(tmp_path) -> None:
    assert backup_secrets(tmp_path / "nope.yaml", tmp_path / "b.bak") is False


def test_backup_skips_broken_yaml(tmp_path) -> None:
    """解析失败的文件不许覆盖最后一份好备份。"""
    src, bak = tmp_path / "secrets.yaml", tmp_path / "secrets.yaml.bak"
    _write(bak, _GOOD)
    src.write_text("push: [broken", encoding="utf-8")
    assert backup_secrets(src, bak) is False
    assert yaml.safe_load(bak.read_text(encoding="utf-8")) == _GOOD


def test_drift_detects_clobbered_password(tmp_path) -> None:
    """复现真实事故：用户粘贴新段把 email 段顶掉，漂移检查必须点名 password。"""
    src, bak = tmp_path / "secrets.yaml", tmp_path / "secrets.yaml.bak"
    _write(bak, _GOOD)
    clobbered = {"push": {"dingtalk": {"enabled": True, "webhook_url": "https://oapi/x2"}}, "intent": {"llm": {"api_key": "sk-1"}}}
    _write(src, clobbered)
    lost = check_secrets_drift(src, bak)
    assert "push.email.password" in lost
    assert "push.email.username" in lost
    assert "push.dingtalk.webhook_url" not in lost  # 当前也有值的不算丢


def test_drift_silent_when_all_present(tmp_path) -> None:
    src, bak = tmp_path / "secrets.yaml", tmp_path / "secrets.yaml.bak"
    _write(bak, _GOOD)
    _write(src, _GOOD)
    assert check_secrets_drift(src, bak) == []


def test_drift_ignores_newly_filled_field(tmp_path) -> None:
    """当前有值、备份为空 = 新增配置，不是丢失。"""
    src, bak = tmp_path / "secrets.yaml", tmp_path / "secrets.yaml.bak"
    partial = {"push": {"email": {"username": "a@126.com"}}}
    _write(bak, partial)
    full = {"push": {"email": {"username": "a@126.com", "password": "new"}}}
    _write(src, full)
    assert check_secrets_drift(src, bak) == []


def test_drift_no_backup_returns_empty(tmp_path) -> None:
    src = tmp_path / "secrets.yaml"
    _write(src, _GOOD)
    assert check_secrets_drift(src, tmp_path / "absent.bak") == []
