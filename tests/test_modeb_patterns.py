"""模式卡与演示件链路:抽原子 → 查模式 → 透传 demo_ref → 无资产/野引用降级。

裁定(2026-07-20 房主照准):演示资产挂模式不挂原子——原子说"要什么东西",
模式说"怎么做动作",demo_ref 属于"怎么做";资产永远只认 pattern_id,
不开"直挂原子"的后门。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor, load_atom_pool, load_pattern_cards  # noqa: E402


def _executor(**kw):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=8, time_budget_min=30, **kw)
    return ToolExecutor(state)


def test_pattern_cards_load_and_are_wellformed():
    cards = load_pattern_cards()
    assert len(cards) == 8, "T1 首批 8 张"
    ids = [c["pattern_id"] for c in cards]
    refs = [c["demo_ref"] for c in cards]
    assert len(set(ids)) == 8 and len(set(refs)) == 8, "pattern_id 与 demo_ref 均不得撞车"
    assert all(c["variants"] for c in cards)


def test_every_variant_exists_in_atom_pool():
    """挂载点完整性:模式卡引用了不存在的原子 = 死链,演示件永远抽不到。"""
    pool_ids = {a["id"] for a in load_atom_pool()}
    for c in load_pattern_cards():
        missing = [v for v in c["variants"] if v not in pool_ids]
        assert not missing, f"{c['pattern_id']}({c['name']}) 变体不在弹药库: {missing}"


def test_draw_atom_carries_demo_from_pattern():
    ex = _executor()
    ex.atom_pool = [a for a in ex.atom_pool if a["id"] == "xhs-01758"]  # 交叉握手对峙
    r = ex.execute({"name": "draw_atom", "input": {}})
    assert r["ok"], r
    demo = r["result"].get("demo")
    assert demo and demo["ref"] == "demo/t1/cross-grip-standoff.svg"
    assert demo["pattern"] == "交叉握手对峙"


def test_draw_atom_without_pattern_has_no_demo():
    ex = _executor()
    ex.atom_pool = [a for a in ex.atom_pool if a["id"] == "seed-02"]  # 行走的弹幕,无模式卡
    r = ex.execute({"name": "draw_atom", "input": {}})
    assert r["ok"] and "demo" not in r["result"]


def test_show_passes_registered_demo_ref():
    ex = _executor()
    r = ex.execute({"name": "show", "input": {
        "content": "交叉握手,先松手的输", "demo": "demo/t1/cross-grip-standoff.svg"}})
    assert r["ok"] and r["result"]["demo_ref"] == "demo/t1/cross-grip-standoff.svg"


def test_show_drops_invented_demo_ref():
    """模型编的资产引用不得透传——降级纯文字,留痕不掀桌。"""
    ex = _executor()
    r = ex.execute({"name": "show", "input": {
        "content": "看我发明的图", "demo": "demo/t9/幻觉.png"}})
    assert r["ok"] and "demo_ref" not in r["result"]
    assert "资产册" in r["result"]["note"]
