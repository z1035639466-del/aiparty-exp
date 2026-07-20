"""弹药库分面修复:分档×类型解耦 + 机制人数下限 + show 批量私发 + ask 未答名单。

三桌实测战损驱动:2331 条弹药三桌只抽出 2 条(分档与类型被实现成强相关,
prompt 教的两招组合必空返);4 人卧底信息坍缩踩中规格死约束;8 人桌发牌
4 回合死气;被窗口挤掉的人被主持读成"安静得可疑"。
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor, _min_players_of, load_atom_pool  # noqa: E402


class _IdleDriver:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _executor(n_players=4):
    names = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"][:n_players]
    state = GameState(players=names, wildness_cap=8, time_budget_min=30)
    return ToolExecutor(state)


# —— ① 分档×类型交叉表:主打五大类两档都不许为空 ——

def test_tier_type_cross_table_has_no_empty_cell():
    cross = Counter((a["type"], a["tier"]) for a in load_atom_pool())
    for typ in ("完整玩法", "条件点名", "任务内容", "道具挑战", "问答题目"):
        for tier in ("铺垫", "主打"):
            assert cross[(typ, tier)] > 0, \
                f"{typ}×{tier} 为空——分档和类型是正交维度,不许实现成强相关"


def test_the_exact_death_combo_now_draws():
    """三个主持第一次调用全死在「完整玩法+铺垫」上,8 人桌因此整局放弃弹药库。"""
    r = _executor().execute({"name": "draw_atom",
                             "input": {"atom_type": "完整玩法", "tier": "铺垫"}})
    assert r["ok"], f"通用局垫场拍必须抽得出: {r}"
    assert r["result"]["atom"]["tier"] == "铺垫"


# —— ② 机制三型 → 人数下限(DM-skill v2.1.1 判据落码)——

def test_min_players_by_mechanism():
    assert _min_players_of({"text": "谁是卧底:大家轮流描述词语", "name": "卧底"}) == 6, \
        "候选池型 N≤5 禁作核心循环(4 人桌信息坍缩实锤)"
    assert _min_players_of({"text": "两人石头剪刀布争队长,分队车轮战", "name": ""}) == 4
    assert _min_players_of({"text": "想一句话依次传话,传给下一个人", "name": ""}) == 3
    assert _min_players_of({"text": "一人摊开手掌,其余人把食指放掌下", "name": "抓手指"}) == 3
    assert _min_players_of({"text": "用手指在对方手心写字让他猜", "name": "猜字"}) == 2, \
        "二十问/猜码类 2 人亦成立(2 人桌实测跑得顺)"


def test_two_player_table_never_draws_crowd_games():
    ex = _executor(2)
    for _ in range(40):
        r = ex.execute({"name": "draw_atom", "input": {}})
        if not r["ok"]:
            break
        aid = r["result"]["atom"]["id"]
        atom = next(a for a in ex.atom_pool if a["id"] == aid)
        assert atom["min_players"] <= 2, f"2 人桌抽到了 {atom['name']}(需 {atom['min_players']} 人)"


# —— ③ show 批量私发(8 人桌发牌 4 回合死气的解药)——

def test_show_batch_private():
    ex = _executor(4)
    r = ex.execute({"name": "show", "input": {
        "content": "词:西瓜", "visibility": "自己看", "players": ["甲", "乙", "丙"]}})
    assert r["ok"] and r["result"]["players"] == ["甲", "乙", "丙"]
    bad = ex.execute({"name": "show", "input": {
        "content": "x", "visibility": "自己看", "players": ["甲", "路人"]}})
    assert bad["ok"] is False and "路人" in bad["clamped"]


# —— ④ ask_result 带被问未答名单(挤掉≠沉默)——

def test_ask_result_reports_silent_players(tmp_path):
    state = GameState(players=["甲", "乙", "丙", "丁"], wildness_cap=6, time_budget_min=30)
    eng = Engine(state, _IdleDriver(), tmp_path / "ep.jsonl")
    eng.tools.execute({"name": "ask", "input": {"prompt": "谁最可疑?", "player": "全场",
                                                "window": 1}})
    eng.push_event({"type": "say", "player": "甲", "text": "丙", "to": "局长"})
    eng.push_event({"type": "vote", "player": "乙", "value": "丙"})
    state.open_ask["deadline"] = time.time() - 0.1  # 窗口到点
    result = eng._close_ask()
    assert result["winner"] == "丙"
    assert sorted(result["silent"]) == ["丁", "丙"], \
        "被问未答的人必须点名可见——主持才能分辨'没赶上'和'故意不说'"
