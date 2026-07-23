"""开局口令闸(公网认证裁定 2026-07-23):隧道公开后 /api/start 是唯一烧钱入口。

设 ZAKZOK_START_KEY 后开局必须对口令;不设则老行为不变(向后兼容);
入座/事件永远不设闸——房间码即门票,朋友零摩擦。口令不进任何留痕面。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.simulator import Hub  # noqa: E402


def _hub(tmp_path) -> Hub:
    return Hub(tmp_path)


def test_no_key_env_keeps_old_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("ZAKZOK_START_KEY", raising=False)
    out = _hub(tmp_path).start({"players": ["甲", "乙"], "driver": "manual"})
    assert out.get("room_code")


def test_key_gate_blocks_and_admits(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAKZOK_START_KEY", "开门")
    hub = _hub(tmp_path)
    with pytest.raises(ValueError):
        hub.start({"players": ["甲", "乙"], "driver": "manual"})
    with pytest.raises(ValueError):
        hub.start({"players": ["甲", "乙"], "driver": "manual", "key": "错的"})
    out = hub.start({"players": ["甲", "乙"], "driver": "manual", "key": "开门"})
    assert out.get("room_code")
    # 口令不进留痕面:snapshot cfg / episode 里都不该有
    import json
    code = out["room_code"]
    s = hub.rooms[code]
    assert "开门" not in json.dumps(out, ensure_ascii=False)
    assert not hasattr(s, "key")
