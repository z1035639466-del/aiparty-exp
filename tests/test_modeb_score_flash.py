"""分数变动播报:账动了要当场有人告诉当事人(真机复盘 2026-07-24)。

病历:「中途分数变动无实时提示」——手机上只有一个当前值,数字自己变了没人知道
为什么;认罚自动扣的那 1 分尤其冤,主持宣布与否全凭自觉(「认罚扣分播报不统一」)。

修法(第1级引擎钳制,不进提示词):改账收口到 state.score(),每笔留
{player, delta, why, at} 流水;player_view 下发最近 SCORE_FLASH_S 秒的那一截,
到期不再下发——手机直接渲染,不用本地计时。账本公开,故全桌的变动都给。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import SCORE_FLASH_S, GameState  # noqa: E402


class _Empty:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    st = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, _Empty(), tmp_path / "ep.jsonl")


def test_forfeit_autoscore_leaves_a_readable_trace(tmp_path):
    e = _engine(tmp_path)
    e.push_event({"type": "forfeit", "player": "甲"})
    flash = e.state.score_flash()
    assert flash == [{"player": "甲", "delta": -1, "why": "认罚跳过"}]
    assert e.state.scores["甲"] == -1, "播报归播报,账照扣"


def test_add_score_carries_the_hosts_own_reason(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "state.add_score",
                     "input": {"player": "乙", "delta": 2, "reason": "学猫叫学得最像"}})
    assert e.state.score_flash() == [{"player": "乙", "delta": 2, "why": "学猫叫学得最像"}]


def test_add_score_without_reason_still_says_something(tmp_path):
    e = _engine(tmp_path)
    e.tools.execute({"name": "state.add_score", "input": {"player": "乙", "delta": -1}})
    assert e.state.score_flash()[0]["why"] == "扣分", "没填理由也不能空着一条无名变动"


def test_settle_is_a_broadcast_too(tmp_path):
    """清账把账面抹平也是一次变动——不播报的话玩家只看见欠的分凭空消失。"""
    e = _engine(tmp_path)
    e.push_event({"type": "forfeit", "player": "甲"})
    e.push_event({"type": "forfeit", "player": "甲"})
    e.tools.execute({"name": "state.settle", "input": {"player": "甲"}})
    assert e.state.scores["甲"] == 0
    last = e.state.score_flash()[-1]
    assert last["player"] == "甲" and last["delta"] == 2 and "清账" in last["why"]
    assert e.state.settled["甲"] == 2, "清账的口数照旧进 settled"


def test_flash_expires_and_log_stays_bounded(tmp_path):
    e = _engine(tmp_path)
    e.push_event({"type": "forfeit", "player": "甲"})
    e.state.score_log[-1]["at"] -= SCORE_FLASH_S + 1
    assert e.state.score_flash() == [], "过期的账不该压在这一轮头上"
    for _ in range(60):
        e.push_event({"type": "forfeit", "player": "乙"})
    assert len(e.state.score_log) <= 40, "流水只留个尾巴,不是第二本账本"


def test_zero_delta_and_unknown_player_write_nothing(tmp_path):
    e = _engine(tmp_path)
    e.state.score("甲", 0, "空改")
    e.state.score("路人", -1, "不在座")
    assert e.state.score_log == [] and "路人" not in e.state.scores


def test_player_view_ships_the_flash(tmp_path):
    from modeb.simulator import Session

    s = Session(players=["甲", "乙", "丙"], minutes=30, wildness=6, objects=[],
                driver_kind="manual", out_dir=tmp_path)
    s.engine.push_event({"type": "forfeit", "player": "甲"})
    v = s.player_view("乙")
    assert v["score_flash"] == [{"player": "甲", "delta": -1, "why": "认罚跳过"}], \
        "别人的变动也给:账本本来就是公开的,桌上听得见"
