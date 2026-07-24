"""收局即清场(真机病历 2026-07-24)。

state.finish 原来只设 finished=True:「焦点在你身上」黄条永久钉死在最后被点名
的人头上,问询窗/计时器/对决全悬着,放榜之后还有人在抢答。裁定:收了的局
不许再欠着任何等待——finish 一拍把进行中的一切当场归零。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _tools():
    st = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    return ToolExecutor(st), st


def test_finish_clears_everything_pending(tmp_path):
    tools, st = _tools()
    st.focus = "丙"
    st.timers.append(time.time() + 60)
    st.open_ask = {"prompt": "谁赢了?", "asked": "全场", "options": None,
                   "deadline": None, "answers": {}, "window": 10}
    st.duel = {"players": ["甲", "乙"], "draw_at": time.time() + 5, "taps": {}}
    st.pending_photo = {"player": "甲", "prompt": "拍个造型"}
    st.pending_audio = {"player": "乙", "prompt": "学猫叫"}
    st.pending_bell = {"at": time.time() + 30, "fx": "停"}

    out = tools.execute({"name": "state.finish", "input": {}})
    assert out["ok"], out
    assert st.finished is True
    assert st.focus is None, "焦点黄条不许钉死"
    assert st.open_ask is None and st.timers == [] and st.duel is None
    assert st.pending_photo is None and st.pending_audio is None
    assert st.pending_bell is None


def test_finish_keeps_ledger(tmp_path):
    """清的是进行中的等待,不是账本:分数/已用原子这些盘点资产一个不动。"""
    tools, st = _tools()
    st.scores["甲"] = 3
    st.notes["赌注"] = "输家学狗叫"
    tools.execute({"name": "state.finish", "input": {}})
    assert st.scores["甲"] == 3 and st.notes["赌注"] == "输家学狗叫"
