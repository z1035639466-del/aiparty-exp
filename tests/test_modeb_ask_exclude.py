"""问询当事人回避(真机病历 2026-07-24:「我出题,问全场会问到我自己」)。

Jack 说真假句,猜题的 ask(player=全场) 连 Jack 自己的手机都出了作答按钮。
修法(机制层):ask 新增 exclude 名单——引擎不收回避者的答案(他的话照旧当
普通发言)、静默名单不冤枉他、view 透传名单让 App 收走他的按钮、轮流模式
把他从顺序里剔除。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _Empty:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    st = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, _Empty(), tmp_path / "ep.jsonl")


def test_excluded_player_answer_not_absorbed(tmp_path):
    e = _engine(tmp_path)
    r = e.tools.execute({"name": "ask", "input": {
        "player": "全场", "prompt": "甲说的哪句是真的?",
        "options": ["第一句", "第二句"], "exclude": ["甲"], "window": 5}})
    assert r["ok"] and e.state.open_ask["exclude"] == ["甲"]
    e.push_event({"type": "say", "player": "甲", "text": "第一句", "to": "局长"})
    assert e.state.open_ask["answers"] == {}, "出题人不许猜自己的题"
    e.push_event({"type": "say", "player": "乙", "text": "第二句", "to": "局长"})
    assert e.state.open_ask["answers"] == {"乙": "第二句"}
    # 窗口烧完收题:回避者不进 silent(他本来就不该答)
    e.state.open_ask["deadline"] = time.time() - 0.01
    closed = e._close_ask()
    assert closed["winner"] == "第二句"
    assert "甲" not in closed["silent"] and "丙" in closed["silent"]


def test_round_mode_skips_excluded(tmp_path):
    e = _engine(tmp_path)
    r = e.tools.execute({"name": "ask", "input": {
        "prompt": "轮流形容甲", "mode": "轮流", "exclude": ["甲"], "window": 5}})
    assert r["ok"]
    ask = e.state.open_ask
    assert "甲" not in ask["order_all"], "轮流顺序剔除当事人"
    assert ask["order_all"] == ["乙", "丙"]


def test_view_carries_exclude(tmp_path):
    """view 层透传由 test_modeb_lobby 系用 Session 全链覆盖;这里锚定字段形状:
    open_ask 无 exclude 参数时也必须带空列表(App 端不用判 undefined)。"""
    e = _engine(tmp_path)
    e.tools.execute({"name": "ask", "input": {"player": "全场", "prompt": "谁赢了?"}})
    assert e.state.open_ask.get("exclude") == []
