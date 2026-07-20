"""快枪手对决(手机原生旗舰件):随机时点保密、毫秒判先、抢跑判负。

公平三则:拔枪时点连主持都不知道(返回值无 draw_at,荷官回执因此拿不到);
玩家端只看到 drawn 布尔翻面;判定只认 t_ms,单发定胜负。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


class _IdleDriver:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    return Engine(state, _IdleDriver(), tmp_path / "ep.jsonl")


def test_duel_start_hides_draw_time(tmp_path):
    eng = _engine(tmp_path)
    r = eng.tools.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    assert r["ok"] and "draw_at" not in json.dumps(r["result"]), \
        "拔枪时点不得出现在返回值(主持/回执都不许知道)"
    d = eng.state.digest(30.0)["duel"]
    assert d["vs"] == ["甲", "乙"] and d["drawn"] is False, "玩家端只看到 drawn 布尔"


def test_duel_clamps():
    ex = ToolExecutor(GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30))
    assert not ex.execute({"name": "duel.start", "input": {"players": ["甲"]}})["ok"]
    assert not ex.execute({"name": "duel.start", "input": {"players": ["甲", "甲"]}})["ok"]
    assert not ex.execute({"name": "duel.start", "input": {"players": ["甲", "路人"]}})["ok"]
    ex.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    again = ex.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    assert not again["ok"] and "进行中" in again["clamped"]
    assert ex.execute({"name": "duel.cancel", "input": {}})["result"]["cancelled"] is True
    assert ex.state.duel is None


def test_false_start_loses(tmp_path):
    eng = _engine(tmp_path)
    eng.tools.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    eng.push_event({"type": "tap", "player": "甲"})  # 枪未响就拍 = 抢跑
    results = [e for e in eng.event_queue if e.get("type") == "duel_result"]
    assert results and results[0]["winner"] == "乙" and "抢跑" in results[0]["reason"]
    assert eng.state.duel is None, "单发定胜负,对决即刻清场"
    assert eng.turn_ready(), "duel_result 要叫醒主持宣布胜负"


def test_first_valid_tap_wins(tmp_path):
    eng = _engine(tmp_path)
    eng.tools.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    eng.state.duel["draw_at"] = time.time() - 0.5  # 枪已响
    eng.push_event({"type": "tap", "player": "乙"})
    eng.push_event({"type": "tap", "player": "甲"})  # 晚到:对决已清场,当普通 tap
    results = [e for e in eng.event_queue if e.get("type") == "duel_result"]
    assert len(results) == 1 and results[0]["winner"] == "乙"
    assert "先拔" in results[0]["reason"]
    taps = [e for e in eng.event_queue if e.get("type") == "tap"]
    assert any(e.get("_absorbed") for e in taps if e["player"] == "乙"), \
        "对决拍被吸收,不单独叫醒主持"


def test_bystander_tap_untouched(tmp_path):
    eng = _engine(tmp_path)
    eng.tools.execute({"name": "duel.start", "input": {"players": ["甲", "乙"]}})
    eng.push_event({"type": "tap", "player": "丙"})  # 吃瓜群众拍手
    assert eng.state.duel is not None, "非对决玩家的 tap 不得触发判定"
    assert not any(e.get("type") == "duel_result" for e in eng.event_queue)
