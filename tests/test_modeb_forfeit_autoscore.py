"""认罚自动记账(房主裁定 2026-07-24)。

真机病历:局长嘴上说「不敢说就喝一口」,账本一分不动——认罚按钮只发信号,
记不记分全凭主持自觉,按钮形同虚设。裁定:跳过挑战/惩罚的才喝和扣分,这笔账
是确定性规则,引擎守卫兑现(修法优先级第1级):按下认罚键系统当场扣1分,
事件带 auto_scored 注记防主持重复扣,主持只管宣布。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _Empty:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    st = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, _Empty(), tmp_path / "ep.jsonl")


def test_forfeit_auto_deducts_and_annotates(tmp_path):
    e = _engine(tmp_path)
    e.push_event({"type": "forfeit", "player": "甲"})
    assert e.state.scores["甲"] == -1, "认罚当场自动扣1分,不劳主持记"
    ev = next(x for x in e.event_queue if x.get("type") == "forfeit")
    assert ev.get("auto_scored") == -1
    assert "别再重复扣分" in ev.get("note", ""), "注记防主持重复扣"
    e.push_event({"type": "forfeit", "player": "甲"})
    assert e.state.scores["甲"] == -2, "再认罚再扣,一次一分"


def test_bystander_done_annotated(tmp_path):
    """定向问询洞的另一半:点名派活时旁人替当事人点完成,信号不拦但必须可分辨。"""
    e = _engine(tmp_path)
    e.state.focus = "乙"
    e.push_event({"type": "done", "player": "甲"})
    ev = next(x for x in e.event_queue if x.get("type") == "done")
    assert ev.get("bystander") is True
    assert "认错人" in ev.get("note", "")
    e.push_event({"type": "done", "player": "乙"})  # 当事人自己按:干净无注记
    clean = [x for x in e.event_queue if x.get("type") == "done" and x["player"] == "乙"]
    assert clean and not clean[0].get("bystander")
    # 旁观者认罚:自动扣分(扣自己的)与旁观注记并存
    e.push_event({"type": "forfeit", "player": "甲"})
    fv = next(x for x in e.event_queue if x.get("type") == "forfeit")
    assert fv.get("auto_scored") == -1 and fv.get("bystander") is True
    assert "别再重复扣分" in fv["note"] and "认错人" in fv["note"]


def test_unknown_player_forfeit_no_crash(tmp_path):
    e = _engine(tmp_path)
    e.push_event({"type": "forfeit", "player": "路人"})  # 不在座:计数照旧,不动账本
    assert e.marks["forfeits"] == 1
    assert "路人" not in e.state.scores
