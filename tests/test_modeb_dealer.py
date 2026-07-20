"""荷官回执(六桌实测裁定):遮蔽按观看者定,不按出口定。

玩家面/驾驶舱照旧遮,发牌人看自己发的牌——回执只走 driver 专用信道,
不进 line/events_in/任何 HTTP 面。
(冷场闹钟已撤,房主裁定:时限唯一机制是显式 timer,没设=开放式等待,
一直等是正确行为;agent 桌冻死归 harness 座位保活,不归引擎。)
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


# —— 开放式等待(冷场闹钟已撤的回归钉子)——

def test_host_waits_forever_without_timer(tmp_path):
    """没设 timer 的静默桌永远不叫醒主持——"没回应就等,就这么简单"。"""
    eng = _engine(tmp_path, [{"text": "下一位,做个鬼脸!", "tool_use": []}])
    eng.turn()
    assert not eng.turn_ready(), "无事件无计时器 = 一直等,不存在隐式冷场唤醒"
