"""唤醒不许浪费:timer_expired 被空拍吞掉 → 唤醒券补发一次,再空就认。

真机死锁病历(2026-07-24):局长被计时器叫醒后交了完全空拍(无词无工具无新
timer),三个唤醒条件(首拍/新事件/计时器)从此全灭,全局死锁。修法不是冷场
闹钟(房主 2026-07-20 撤销过,不许借尸还魂):无隐式定时,只是已经响过的铃
不许被空拍吞掉——补发一次,第二次仍空依「没回应就等」认了。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _EmptyDriver:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, digest, events):
        self.calls += 1
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    st = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, _EmptyDriver(), tmp_path / "ep.jsonl")


def test_wasted_wake_requeued_once_then_rest(tmp_path):
    e = _engine(tmp_path)
    e.turn()  # 首拍(空拍合法)
    e.state.timers.append(time.time() - 0.01)  # 一个已到点的计时器
    assert e.turn_ready()
    e.turn()  # 空拍吞掉 timer_expired → 唤醒券补发
    requeued = [x for x in e.event_queue if x.get("type") == "timer_expired" and x.get("requeued")]
    assert len(requeued) == 1, "唤醒券该补发一次"
    assert e.turn_ready(), "补发的唤醒券必须能叫醒主持"
    e.turn()  # 第二次仍空拍:不再补发
    assert not any(x.get("requeued") for x in e.event_queue), "只补一次,不许循环"
    assert not e.turn_ready(), "两次空拍后回到「没回应就等」"


def test_nonempty_beat_resets_retry(tmp_path):
    e = _engine(tmp_path)

    class _Talker:
        def decide(self, digest, events):
            return {"text": "收口了。", "tool_use": []}

    e.turn()
    e.state.timers.append(time.time() - 0.01)
    e.turn()  # 空拍→补发
    e.driver = _Talker()
    e.turn()  # 正常拍消费补发券并复位标记
    assert not e.turn_ready()
    e.state.timers.append(time.time() - 0.01)
    e.driver = _EmptyDriver()
    e.turn()  # 新一轮空拍:标记已复位,应再次补发
    assert any(x.get("requeued") for x in e.event_queue)
