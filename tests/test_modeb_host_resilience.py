"""主持断线三层策略:传输层重试一次 → 引擎沉默拍(事件塞回+冷却)→ 台面记账。

裁定形态:错误不进游戏,只进台面。桌上永远只有「主持在说话」和
「主持在等」两种状态,第三种(主持报错)只存在于驾驶舱仪表盘。
"""
from __future__ import annotations   # int | None 写在运行时求值的注解里,3.9 会 TypeError

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.transports import Resilient, TransportError  # noqa: E402


# —— 传输层:瞬时重试一次,永久直接上抛 ——

class _Flaky:
    def __init__(self, fail_times: int, code: int | None) -> None:
        self.fail_times, self.code, self.calls = fail_times, code, 0

    def complete(self, system, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransportError(f"boom #{self.calls}", self.code)
        return "ok"


def test_resilient_retries_transient_once():
    inner = _Flaky(1, 429)
    assert Resilient(inner, wait_s=0).complete("s", []) == "ok"
    assert inner.calls == 2, "429 是瞬时错,该重试一次"


def test_resilient_gives_up_after_second_failure():
    inner = _Flaky(2, None)  # 网络层错误,连挂两次
    with pytest.raises(TransportError):
        Resilient(inner, wait_s=0).complete("s", [])
    assert inner.calls == 2, "重试只有一次,不无限撞"


def test_resilient_no_retry_on_permanent():
    inner = _Flaky(9, 401)  # key 错:重试一万次也一样
    with pytest.raises(TransportError):
        Resilient(inner, wait_s=0).complete("s", [])
    assert inner.calls == 1, "永久错误不许重试"


# —— 引擎层:沉默拍 ——

class _FailOnceDriver:
    def __init__(self) -> None:
        self.fail, self.seen = True, []

    def decide(self, digest, events):
        if self.fail:
            self.fail = False
            raise TransportError("api.example 返回 HTTP 429:限流", 429)
        self.seen.append(list(events))
        return {"text": "继续。", "tool_use": []}


def test_silent_beat_requeues_events_and_cools_down(tmp_path):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    eng = Engine(state, _FailOnceDriver(), tmp_path / "ep.jsonl")
    eng.push_event({"type": "done", "player": "甲"})

    line = eng.turn()  # 第一拍:decide 挂 → 沉默拍
    assert line["host_silent"] is True and "429" in line["host_error"]
    assert line["events_requeued"] == 1
    assert eng.event_queue and eng.event_queue[0]["type"] == "done", "事件一个不能丢"
    assert eng.marks["host_errors"] == 1 and eng.host_error_streak == 1
    assert eng.marks["turns"] == 0, "沉默拍不算回合"
    assert not eng.turn_ready(), "冷却期内不叫醒主持"

    eng.cooldown_until = 0.0  # 冷却到点
    assert eng.turn_ready(), "冷却一过,队列里的事件自然再次叫醒主持"
    line2 = eng.turn()  # 第二拍:成功,拿到的正是刚才那批事件
    assert "host_silent" not in line2
    assert any(e.get("type") == "done" and e.get("player") == "甲"
               for e in line2["events_in"]), "重来的一拍必须看到原事件"
    assert eng.host_error_streak == 0, "成功一拍归零连败计数"
    assert eng.marks["host_errors"] == 1, "累计数保留,进台面"
