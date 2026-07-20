"""技能转手(skill.transfer)复活验收:抢夺/交换/截胡三卡此前因缺账面原语被砍。

砍点只有一个——引擎没有"把一张 grant 从 A 名下挪到 B 名下"的动作,主持嘴上转、
账本(digest.grants)没转 = 嘴账不一。补上原语后此处钉死:转移成功、源无此牌驳回、
目标同名驳回、digest 归属跟着变、玩家视角私件只看自己那份。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState, SkillGrant  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _ex(names=("甲", "乙", "丙")):
    state = GameState(players=list(names), wildness_cap=8, time_budget_min=30)
    return ToolExecutor(state)


def _grant(ex, prop, holder, uses=2, ritual="虚点画圈轻念臣服"):
    ex.state.grants.append(SkillGrant(prop=prop, holder=holder, bound_object="",
                                      uses_left=uses, ritual=ritual))


# —— ① 转移成功:ledger 与 digest 归属一起翻面 ——

def test_transfer_moves_grant_and_digest_follows():
    ex = _ex()
    _grant(ex, "沉默术", "甲")
    r = ex.execute({"name": "skill.transfer",
                    "input": {"from": "甲", "to": "乙", "prop": "沉默术"}})
    assert r["ok"] and r["result"]["to"] == "乙" and r["result"]["uses_left"] == 2
    # ledger:这张牌 holder 翻到乙,次数不变
    g = next(g for g in ex.state.grants if g.prop == "沉默术")
    assert g.holder == "乙" and g.uses_left == 2
    # digest.grants 归属跟着变——嘴上转、账上也真转
    grants = ex.state.digest(30)["grants"]
    assert {"prop": "沉默术", "holder": "乙", "uses_left": 2} in grants
    assert not any(x["holder"] == "甲" for x in grants), "转出方名下不该再挂这张"


def test_transfer_default_prop_takes_first_available():
    ex = _ex()
    _grant(ex, "沉默术", "甲")
    # 不指定 prop:转源玩家名下第一张可用技能
    r = ex.execute({"name": "skill.transfer", "input": {"from": "甲", "to": "丙"}})
    assert r["ok"] and r["result"]["transferred"] == "沉默术" and r["result"]["to"] == "丙"


# —— ② 源无此牌驳回(只回执,不当众出丑)——

def test_transfer_source_lacks_card_rejected():
    ex = _ex()
    _grant(ex, "沉默术", "甲")
    # 乙名下没有任何可用技能 → 驳回
    r = ex.execute({"name": "skill.transfer", "input": {"from": "乙", "to": "丙"}})
    assert r["ok"] is False and "没有" in r["clamped"]
    # 指定一张源没有的牌 → 同样驳回,且点名那张牌
    r2 = ex.execute({"name": "skill.transfer",
                     "input": {"from": "甲", "to": "乙", "prop": "嘲讽"}})
    assert r2["ok"] is False and "嘲讽" in r2["clamped"]
    # 用尽(uses_left=0)的牌不算可用,不许转
    _grant(ex, "野蛮冲撞", "乙", uses=0)
    r3 = ex.execute({"name": "skill.transfer",
                     "input": {"from": "乙", "to": "丙", "prop": "野蛮冲撞"}})
    assert r3["ok"] is False


# —— ③ 目标同名驳回(沿用同名不重发,与 skill.deal 一致)——

def test_transfer_target_has_samename_rejected():
    ex = _ex()
    _grant(ex, "沉默术", "甲")
    _grant(ex, "沉默术", "乙")
    r = ex.execute({"name": "skill.transfer",
                    "input": {"from": "甲", "to": "乙", "prop": "沉默术"}})
    assert r["ok"] is False and "同名" in r["clamped"]
    # 甲那张原地不动,没被半途挪走
    assert any(g.holder == "甲" and g.prop == "沉默术" for g in ex.state.grants)


# —— ④ 边角驳回:自转自 / 非在座 / 未知子操作 ——

def test_transfer_self_and_unknown_rejected():
    ex = _ex()
    _grant(ex, "沉默术", "甲")
    assert ex.execute({"name": "skill.transfer",
                       "input": {"from": "甲", "to": "甲", "prop": "沉默术"}})["ok"] is False
    assert ex.execute({"name": "skill.transfer",
                       "input": {"from": "甲", "to": "路人"}})["ok"] is False
    assert ex.execute({"name": "skill.wat", "input": {}})["ok"] is False, "未知子操作要驳回"


# —— ⑤ 玩家视角遮蔽:转出/转入方各自私件,只看自己那份 ——

def test_transfer_private_notices_masked_per_viewer(tmp_path):
    from modeb.simulator import Session
    s = Session(["甲", "乙", "丙"], 30, 8, [], "manual", tmp_path)
    s.engine.tools.state.grants.append(
        SkillGrant(prop="沉默术", holder="甲", bound_object="", uses_left=2,
                   ritual="手掌虚按向对方嘴前喊沉默"))
    res = s.engine.tools.execute({"name": "skill.transfer",
                                  "input": {"from": "甲", "to": "乙", "prop": "沉默术"}})
    assert res["ok"]
    # 私件挂账:转出/转入方各记一笔去向(只记去向不记内容)
    kinds = {(e["holder"], e["kind"]) for e in s.state.private_out}
    assert ("甲", "技能转出") in kinds and ("乙", "技能转入") in kinds
    # 走可见性引擎:公开面(轮询/驾驶舱)只剩摘要,私件原文不落公开回合行
    line = {"tool_use": [{"name": "skill.transfer", "input": {"from": "甲", "to": "乙"}}],
            "results": [res]}
    red = s.route_private(line)
    assert "仪式" not in str(red["results"]), "转入方那句发动仪式不许漏到公开面"
    assert "内容仅各自可见" in str(red["results"])
    # 玩家视角:各自收件箱只看到自己那一份,旁观者(丙)什么都收不到
    assert any("被转走" in m for m in s.inbox["甲"])
    assert any("你得到技能" in m for m in s.inbox["乙"])
    assert s.inbox["丙"] == [], "旁观者不该看到别人的转手私件"
