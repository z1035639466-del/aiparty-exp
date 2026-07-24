"""派活账:「这活是谁的」的唯一信号源(真机复盘 2026-07-24)。

病历:App 的「✅完成(X的活)」标签挂在 state.focus 上,而 focus 是局长手写的字段
——64 拍只调过 2 次(t25/t52),同期定向派活发生 5 次。后果:前半场完全没拦
(Ming 替 Ann 按完成被记成「Ming 完成」)、后半场标签滞后十几拍、该锁时不锁、
拦了不说话、同屏两个矛盾的「完成」。

修法(第1级引擎钳制 + 第2级工具声明,不进提示词):派活由引擎从真实调用里自动记
——ask 点名 / judge / duel / prop.dice_cup / draw_atom(for_player) / state.set_focus,
交活即销账,超 ASSIGN_TTL_S 自动过期。焦点归焦点,派活归派活。
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


def test_named_ask_assigns_all_field_ask_does_not(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "ask", "input": {"player": "乙", "prompt": "选谁?"}})
    assert e.state.actors() == ["乙"]
    # 问全场不是派活:全场抢答人人有份,不许锁任何人的按钮
    e.tools.execute({"name": "ask", "input": {"player": "全场", "prompt": "谁赢了?"}})
    assert e.state.actors() == ["乙"], "问全场不该改写派活账"


def test_ask_close_clears_only_its_own_assignment(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "ask", "input": {"player": "乙", "prompt": "选谁?", "window": 1}})
    e.push_event({"type": "say", "player": "乙", "text": "丙", "to": "局长"})
    e.state.open_ask["deadline"] = time.time() - 0.01
    assert e._close_ask()["winner"] == "丙"
    assert e.state.actors() == [], "问询收窗=这笔派活结清"


def test_draw_atom_for_player_is_the_assignment(tmp_path):
    e = _engine(tmp_path)
    r = e.tools.execute({"name": "draw_atom", "input": {
        "atom_type": "任务内容", "for_player": "丙"}})
    assert r["ok"] and r["result"]["assigned_to"] == ["丙"]
    assert e.state.actors() == ["丙"]


def test_solo_atom_without_for_player_gets_nagged_not_assigned(tmp_path):
    e = _engine(tmp_path)
    r = e.tools.execute({"name": "draw_atom", "input": {"atom_type": "任务内容"}})
    assert r["ok"]
    assert "for_player" in r["result"]["assign_note"]
    assert e.state.actors() == [], "不知道派给谁就别认领——宁可不声张也不挂错归属"


def test_bystander_marking_follows_assignment_not_focus(tmp_path):
    """本轮最大发现:按键的是不是当事人,凭派活账认,不凭 focus。"""
    e = _engine(tmp_path)
    e.tools.execute({"name": "draw_atom", "input": {
        "atom_type": "任务内容", "for_player": "甲"}})
    assert e.state.focus is None, "全程没调过 set_focus——旧判据在这里是瞎的"
    e.push_event({"type": "done", "player": "乙"})
    assert e.event_queue[-1].get("bystander") is True
    assert "甲" in e.event_queue[-1]["note"]
    # 当事人自己交活:不标旁观,且这笔派活当场结清
    e.push_event({"type": "done", "player": "甲"})
    assert not e.event_queue[-1].get("bystander")
    assert e.state.actors() == []


def test_assignment_expires_and_finish_clears(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "state.set_focus", "input": {"player": "乙"}})
    assert e.state.actors() == ["乙"]
    e.state.assigned["at"] -= 10_000  # 过期的派活不算数(口头玩法早翻篇了)
    assert e.state.actors() == []
    e.push_event({"type": "done", "player": "甲"})
    assert not e.event_queue[-1].get("bystander"), "过期后谁按都不算认错人"
    e.tools.execute({"name": "state.set_focus", "input": {"player": "乙"}})
    e.tools.execute({"name": "state.finish", "input": {}})
    assert e.state.assigned is None and e.state.actors() == []


def test_batch_cup_assigns_everyone_so_nobody_is_dimmed(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "prop.dice_cup", "input": {
        "players": ["甲", "乙", "丙"], "count": 5}})
    assert e.state.actors() == ["甲", "乙", "丙"], "全员发盅=全员有活,一台手机都不该被按暗"


def test_next_round_clears_stale_assignment(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "state.set_focus", "input": {"player": "甲"}})
    e.tools.execute({"name": "state.next_round", "input": {}})
    assert e.state.actors() == [], "翻轮=上一轮的活作废"


def test_digest_carries_assignment(tmp_path):
    e = _engine(tmp_path)
    assert e.state.digest(10.0)["assigned"] is None
    e.tools.execute({"name": "judge.photo", "input": {"player": "丙", "prompt": "有没有站上椅子"}})
    d = e.state.digest(10.0)
    assert d["assigned"]["players"] == ["丙"] and "丙" in d["assigned"]["why"]
