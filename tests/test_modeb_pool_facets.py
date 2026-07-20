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


# —— ②½ 技能牌供给与教学(实测:agent 局从没见过局长发客制化技能)——

def test_skill_atoms_supply_and_teaching():
    from modeb.driver_llm import build_system_prompt
    from modeb.tools import load_atom_pool
    skills = [a for a in load_atom_pool() if a["type"] == "技能授予"]
    assert len(skills) >= 8, f"技能牌存量至少 8 张(旧存量 4 张=抽中率 0.14%,等于没有),实际 {len(skills)}"
    assert all(a.get("skill", {}).get("ritual") for a in skills), "技能必须带使用条件仪式(正典)"
    p = build_system_prompt(["甲", "乙"], 6, 30)
    assert "【技能牌】" in p and "skill.deal" in p, "prompt 不教发技能,主持就永远不发"


def test_skill_pool_is_separate_and_virtual_form_works():
    """技能单独开库(权力卡与内容不是一个体量):
    ①无类型 draw_atom 永远抽不到技能;②道具不在场时虚拟态照发(双态正典兑现)。"""
    ex = _executor(4)
    assert ex.skill_pool and all(s["type"] == "技能授予" for s in ex.skill_pool)
    assert not any(a["type"] == "技能授予" for a in ex.atom_pool), "技能不得混进内容池稀释"
    for _ in range(30):
        r = ex.execute({"name": "draw_atom", "input": {}})
        if not r["ok"]:
            break
        assert r["result"]["atom"]["type"] != "技能授予"

    # 场上零实物:时间暂停器(要遥控器/打火机)也必须发得出——虚拟态
    ex2 = _executor(3)
    ex2.state.scene_objects = []
    dealt = []
    while True:
        r = ex2.execute({"name": "skill.deal", "input": {"grant_to": "甲"}})
        if not r["ok"]:
            assert "发完" in r["clamped"]
            break
        dealt.append(r["result"])
        ex2.state.grants[-1].uses_left = 0  # 用尽,允许继续发下一张
    assert len(dealt) >= 6, f"零实物场景技能库应整库可发(虚拟态),实际只发出 {len(dealt)}"
    assert all(d["bound_object"] is None and "虚拟态" in d["form"] for d in dealt)
    assert any(d["atom"]["name"] == "时间暂停器" for d in dealt), "时间暂停器不许再被'道具不在场'拦"


def test_draw_atom_skill_type_delegates_to_skill_channel():
    ex = _executor(4)
    r = ex.execute({"name": "draw_atom", "input": {"atom_type": "技能授予", "grant_to": "乙"}})
    assert r["ok"] and r["result"]["granted_to"] == "乙", "老调法委托专用信道,不许断"
    assert ex.state.grants and ex.state.grants[-1].holder == "乙"


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
