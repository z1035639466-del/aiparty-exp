"""荷官回执 + 冷场闹钟(六桌实测三裁定中归引擎的两条)。

回执:遮蔽按观看者定,不按出口定——玩家面/驾驶舱照旧遮,发牌人看自己发的牌。
只走 driver 专用信道,不进 line/events_in/任何 HTTP 面。
冷场:等待权做到了「不催」,但没有「发现没人了」(2 人桌冻在第 7 拍)。
主持行动后静默超时,叫醒一次;催不催由铁律管。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _ScriptDriver:
    """按剧本出决策,并记录每拍收到的 events。"""

    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = list(decisions)
        self.seen: list[list[dict]] = []

    def decide(self, digest, events):
        self.seen.append(list(events))
        return self.decisions.pop(0) if self.decisions else {"text": "", "tool_use": []}


def _engine(tmp_path, decisions):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    return Engine(state, _ScriptDriver(decisions), tmp_path / "ep.jsonl")


# —— 荷官回执 ——

def test_dealer_sees_own_private_content_next_beat(tmp_path):
    eng = _engine(tmp_path, [
        {"text": "私发。", "tool_use": [{"name": "show", "input": {
            "content": "毒杯是3号", "visibility": "自己看", "player": "乙"}}]},
        {"text": "", "tool_use": []},
    ])
    line1 = eng.turn()
    assert not any(e.get("type") == "tool_receipts" for e in eng.driver.seen[0]), \
        "第一拍没有上一拍,不该有回执"
    eng.push_event({"type": "done", "player": "乙"})
    line2 = eng.turn()
    receipts = [e for e in eng.driver.seen[1] if e.get("type") == "tool_receipts"]
    assert receipts and "毒杯是3号" in str(receipts[0]["results"]), \
        "发牌人必须在下一拍看到自己发的牌(私发半盲的解)"
    assert not any(e.get("type") == "tool_receipts" for e in line2["events_in"]), \
        "回执只走 driver 信道,不得进 line/events_in(否则轮询面漏底)"


def test_dealer_sees_clamp_in_receipts(tmp_path):
    eng = _engine(tmp_path, [
        {"text": "发!", "tool_use": [{"name": "show", "input": {
            "content": "x", "visibility": "自己看"}}]},   # 缺 player → 钳制
        {"text": "", "tool_use": []},
    ])
    eng.turn()
    eng.push_event({"type": "laugh", "player": "甲"})
    eng.turn()
    receipts = [e for e in eng.driver.seen[1] if e.get("type") == "tool_receipts"]
    assert receipts and receipts[0]["results"][0]["ok"] is False, \
        "钳制记录也要回执——否则主持嘴上说了、系统没生效还不自知"


# —— 冷场闹钟 ——

def test_silence_alarm_wakes_host_exactly_once(tmp_path):
    eng = _engine(tmp_path, [{"text": "下一位,做个鬼脸!", "tool_use": []}])
    eng.turn()  # 主持行动 → 挂闹钟
    assert eng._silence_deadline is not None
    assert not eng.turn_ready(), "没到点不叫醒"

    eng._silence_deadline = time.time() - 1  # 冷场到点
    assert eng.turn_ready(), "静默超时该叫醒一次"
    line = eng.turn()
    silent_evs = [e for e in line["events_in"] if e.get("type") == "table_silent"]
    assert silent_evs and silent_evs[0]["quiet_s"] >= 0
    assert not eng.turn_ready(), "一段静默只叫醒一次,不无限骚扰"


def test_any_table_activity_disarms_alarm(tmp_path):
    eng = _engine(tmp_path, [{"text": "开始!", "tool_use": []}])
    eng.turn()
    eng.push_event({"type": "laugh", "player": "丙"})
    assert eng._silence_deadline is None, "桌面有动静 = 场子活着,闹钟解除"


def test_host_empty_beat_does_not_rearm(tmp_path):
    eng = _engine(tmp_path, [
        {"text": "挑战开始!", "tool_use": []},
        {"text": "", "tool_use": []},          # 主持静等(空拍)
    ])
    eng.turn()
    d1 = eng._silence_deadline
    eng.push_event({"type": "tap", "player": "甲"})
    eng.turn()  # 空拍
    assert eng._silence_deadline is None or eng._silence_deadline == d1, \
        "空拍(静等)不重挂闹钟——闹钟跟着主持的行动走,不跟空转走"
