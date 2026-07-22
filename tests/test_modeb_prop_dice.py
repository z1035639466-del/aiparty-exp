"""骰盅道具全链路验收:局长发盅、玩家自己摇(房主原则:局长不替玩家玩)。

真机病根:局长用 random.dice 替玩家暗摇、甚至用 show 编假骰子直接快递结果,
玩家全程没有"玩"的动作。这里把玩的动作钉回玩家手里——
发盅(prop.dice_cup 公开挂账,点数此刻不存在)→ 玩家 POST /api/event roll 自己摇 →
点数只进本人私件(🔒🎲 水印)+局长对账信道,公开事件面只见"谁摇了"、无点数。
钉死:发盅可见未摇、一盅一摇、没盅驳回、重发换新盅可再摇、对账信道拿得到点数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _ex(names=("甲", "乙", "丙")) -> ToolExecutor:
    return ToolExecutor(GameState(players=list(names), wildness_cap=6,
                                  time_budget_min=30), rng_seed=7)


def _session(tmp_path, names=("甲", "乙", "丙")):
    from modeb.simulator import Session
    return Session(list(names), 30, 6, [], "manual", tmp_path)


class _Recorder:
    """记录 engine 送进 driver 的 (digest, events)——用来验证对账信道到没到主持手里。"""

    def __init__(self) -> None:
        self.seen: list[tuple] = []

    def decide(self, digest: dict, events: list[dict]) -> dict:
        self.seen.append((digest, events))
        return {"text": "", "tool_use": []}


# —— ① 发盅:工具面钳制(count 1–10、players 须在座、批量)——

def test_deal_cup_clamps_and_seats():
    ex = _ex()
    r = ex.execute({"name": "prop.dice_cup", "input": {"players": ["甲", "乙"], "count": 5}})
    assert r["ok"], r
    assert set(r["result"]["dealt"]) == {"甲", "乙"} and r["result"]["count"] == 5
    for p in ("甲", "乙"):
        assert ex.state.props[p] == {"kind": "骰盅", "count": 5, "rolled": None}
    # count 越界钳制
    for bad in (0, 11, -2):
        rb = ex.execute({"name": "prop.dice_cup", "input": {"players": ["甲"], "count": bad}})
        assert not rb["ok"], f"count={bad} 该被钳制却放行: {rb}"
    # 批量里混不在座者整单驳回
    rn = ex.execute({"name": "prop.dice_cup", "input": {"players": ["甲", "路人"]}})
    assert not rn["ok"]
    # 无 players 无 player:驳回
    assert not ex.execute({"name": "prop.dice_cup", "input": {}})["ok"]


# —— ② 发盅后:视图可见未摇,点数不在任何公共面 ——

def test_dealt_cup_visible_unrolled_no_points(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup",
                            "input": {"players": ["甲", "乙", "丙"], "count": 5}})
    # 本人视图:my_prop 未摇(rolled=None)
    v = s.player_view("甲")
    assert v["my_prop"] == {"kind": "骰盅", "count": 5, "rolled": None}
    # 全桌都看得到"谁有盅、摇没摇"(布尔),没有点数
    cups = {c["player"]: c["rolled"] for c in v["cups"]}
    assert cups == {"甲": False, "乙": False, "丙": False}
    # digest 挂账:谁有盅、几颗、摇没摇——不含点数
    dc = s.state.digest(30)["dice_cups"]
    assert all(set(x) == {"player", "count", "rolled"} and x["rolled"] is False for x in dc)


# —— ③ 摇盅:点数合法且只进本人视图,公开事件与旁人视图都无点数 ——

def test_roll_points_private_only(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲", "乙"], "count": 5}})
    out = s.roll_cup("甲")
    assert out["ok"] and out["count"] == 5
    dice = s.state.props["甲"]["rolled"]
    assert isinstance(dice, list) and len(dice) == 5
    assert all(isinstance(d, int) and 1 <= d <= 6 for d in dice)
    # 本人视图看得到自己的点数(常驻区,大话骰全程盯着吹牛)
    assert s.player_view("甲")["my_prop"]["rolled"] == dice
    # 点数经 🔒🎲 防伪水印进本人私件(App 只认水印画骰面)
    assert any(m.startswith("🔒🎲") and str(dice) in m for m in s.inbox["甲"])
    # 公开事件面只出"甲摇了骰盅"、无点数:事件流里那条 roll 不带点数
    roll_ev = next(e for e in s.engine.event_queue if e.get("type") == "roll")
    assert roll_ev["player"] == "甲" and "value" not in roll_ev
    assert str(dice) not in json.dumps(roll_ev, ensure_ascii=False)
    # 旁人(乙)视图:自己没摇,拿不到甲的点数(整份 view 里不出现甲的点数串)
    v_yi = s.player_view("乙")
    assert v_yi["my_prop"] == {"kind": "骰盅", "count": 5, "rolled": None}
    assert str(dice) not in json.dumps(v_yi, ensure_ascii=False), "旁人视图漏了甲的点数"
    # 甲的点数也不在乙的私件里
    assert s.inbox["乙"] == []


# —— ④ 一盅一摇:摇过再摇驳回(防赖账,重摇须局长重发)——

def test_second_roll_rejected(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲"], "count": 3}})
    assert s.roll_cup("甲")["ok"]
    first = list(s.state.props["甲"]["rolled"])
    r2 = s.roll_cup("甲")
    assert r2["ok"] is False and "摇过" in r2["error"]
    assert s.state.props["甲"]["rolled"] == first, "驳回的二次摇不许改点数"


# —— ⑤ 没盅摇:驳回 ——

def test_roll_without_cup_rejected(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲"], "count": 5}})
    r = s.roll_cup("丙")  # 丙没盅
    assert r["ok"] is False and "没有骰盅" in r["error"]


# —— ⑥ 重发换新盅:rolled 重置,可再摇 ——

def test_redeal_resets_and_allows_reroll(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲"], "count": 5}})
    assert s.roll_cup("甲")["ok"]
    # 局长重发一只新盅(换版:3 颗):rolled 归 None
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲"], "count": 3}})
    assert s.state.props["甲"]["rolled"] is None and s.state.props["甲"]["count"] == 3
    r = s.roll_cup("甲")
    assert r["ok"] and len(s.state.props["甲"]["rolled"]) == 3
    # 收盅(prop.cancel):盅没了,再摇驳回
    s.engine.tools.execute({"name": "prop.cancel", "input": {"players": ["甲"]}})
    assert "甲" not in s.state.props
    assert s.roll_cup("甲")["ok"] is False


# —— ⑦ 局长对账信道:点数走 driver 专属信道(仅主持),不进 digest/公开面 ——

def test_dealer_ledger_reaches_host_not_public(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.dice_cup", "input": {"players": ["甲", "乙"], "count": 5}})
    s.roll_cup("甲")
    s.roll_cup("乙")
    dice_a, dice_b = s.state.props["甲"]["rolled"], s.state.props["乙"]["rolled"]
    # 换上记录型 driver 跑一拍:看主持这拍收到了什么
    rec = _Recorder()
    s.engine.driver = rec
    line = s.engine.turn()
    digest, events = rec.seen[-1]
    ledger = next((e for e in events if e.get("type") == "dice_cup_ledger"), None)
    assert ledger is not None, "对账信道没送到主持手里"
    assert ledger["points"] == {"甲": dice_a, "乙": dice_b}
    # digest(公共面)只有布尔挂账,绝无点数
    assert str(dice_a) not in json.dumps(digest["dice_cups"], ensure_ascii=False)
    assert all(x["rolled"] is True for x in digest["dice_cups"])
    # 公开回合行(落 episode/驱动 HTTP 的 line)里没有点数:对账信道只活在 upstream
    assert str(dice_a) not in json.dumps(line, ensure_ascii=False)
    assert str(dice_b) not in json.dumps(line, ensure_ascii=False)
    # 旁人视图仍旧拿不到点数
    assert str(dice_a) not in json.dumps(s.player_view("乙"), ensure_ascii=False)
