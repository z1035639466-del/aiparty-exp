"""赌注替换:弹药库的『喝』不许流进惩罚层(房主裁定 2026-07-24)。

真机病历:交叉握手赛玩法卡直接写「③ 输的喝一口」——不是局长自由发挥,是
draw_atom 把原子的 currency=喝 原样递了过去,判罚铁律的例外条款(「原子本身
以酒为赌注」)把它合法化了。库源是小红书酒桌玩法,大半原子以喝为赌注,例外
条款等于给向喝坍缩开了正门。

修法(修法优先级第2级,工具口条件触发):draw_atom 抽到喝赌注原子时,回执里
当场换赌注——输家抽共同经历型惩罚;不想接的玩家自己认罚喝一口+扣分兜底。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _executor():
    st = GameState(players=["甲", "乙", "丙"], wildness_cap=8, time_budget_min=30)
    return ToolExecutor(st)


def test_booze_stake_swapped_in_receipt():
    ex = _executor()
    ex.atom_pool = [{"id": "t-喝", "name": "交叉握手赛", "type": "完整玩法",
                     "text": "依次交叉手,快速握住对方的手,打开的喝。",
                     "wildness": 3, "currency": "喝", "tier": "铺垫",
                     "min_players": 2, "props": []}]
    r = ex.execute({"name": "draw_atom", "input": {}})
    assert r["ok"], r
    atom = r["result"]["atom"]
    assert atom["currency"] != "喝", "喝不许作为赌注原样递给局长"
    assert "已按判罚铁律替换" in atom["currency"]
    note = r["result"]["stake_note"]
    assert "共同经历型惩罚" in note and "认罚喝一口" in note and "扣分" in note
    assert "不许出现" in note, "注记必须明说玩法卡上不许写『输的喝』"


def test_non_booze_stake_untouched():
    ex = _executor()
    ex.atom_pool = [{"id": "t-演", "name": "模仿秀", "type": "任务内容",
                     "text": "学一种动物叫,像不像全场评。",
                     "wildness": 2, "currency": "表演", "tier": "铺垫",
                     "min_players": 2, "props": []}]
    r = ex.execute({"name": "draw_atom", "input": {}})
    assert r["ok"], r
    assert r["result"]["atom"]["currency"] == "表演", "非喝赌注一个字不动"
    assert "stake_note" not in r["result"], "没换赌注就没有注记,不注水"
