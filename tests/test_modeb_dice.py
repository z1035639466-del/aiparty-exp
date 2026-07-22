"""random.dice 实装回归:prompt 里让主持私密摇骰(大话骰开局的唯一姿势),
工具层却一直只有 pick/int——调了就被钳,骰子局全被逼成嘴报数(三局 episode
里 random.dice 调用数为零的病根之一)。本测钉死:摇得出、钳得住、私得了。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _ex() -> ToolExecutor:
    return ToolExecutor(GameState(players=["甲", "乙", "丙"],
                                  wildness_cap=6, time_budget_min=30), rng_seed=7)


def test_dice_default_five_six_sided():
    r = _ex().execute({"name": "random.dice", "input": {}})
    assert r["ok"], r
    dice = r["result"]["value"]
    assert isinstance(dice, list) and len(dice) == 5
    assert all(isinstance(d, int) and 1 <= d <= 6 for d in dice)


def test_dice_count_clamped():
    for bad in (0, 11, -3):
        r = _ex().execute({"name": "random.dice", "input": {"count": bad}})
        assert not r["ok"], f"count={bad} 该被钳制却放行: {r}"


def test_dice_private_requires_seated_player():
    ex = _ex()
    r = ex.execute({"name": "random.dice",
                    "input": {"count": 5, "visibility": "自己看", "player": "乙"}})
    assert r["ok"], r
    assert r["result"]["player"] == "乙" and r["result"]["visibility"] == "自己看"
    r2 = ex.execute({"name": "random.dice",
                     "input": {"visibility": "自己看", "player": "路人"}})
    assert not r2["ok"], "不在座的人也能收私骰,遮蔽就漏了"
