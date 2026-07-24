"""死锁哨兵(房主 2026-07-24:「局长可以感知场上有没有游戏在玩,没有游戏
持续时间长有可能就是死锁」)。

与被撤销的冷场闹钟(2026-07-20)的界线:闹钟是催场子填冷场,哨兵是感知
「没有任何游戏结构在跑 + 台面长时间没有任何动作」的死锁风险。三条纪律:
单发不循环;可被无视(回空拍=继续等);有玩家动作即复位。判断权在局长。
挂着任何钩子(计时/问询/对决/未摇盅)都不算死锁——那是游戏在跑。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine, IDLE_CHECK_S  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _Empty:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    st = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, _Empty(), tmp_path / "ep.jsonl")


def test_idle_fires_once_after_threshold(tmp_path):
    e = _engine(tmp_path)
    e.turn()  # 首拍
    assert not e.turn_ready(), "刚开局不叫(阈值未到)"
    e._last_activity = time.time() - IDLE_CHECK_S - 5   # 台面静默超阈值
    assert e.turn_ready(), "死锁哨兵该叫醒局长"
    ev = next(x for x in e.event_queue if x.get("type") == "idle_check")
    assert "游戏结构" in ev["note"] and "空拍" in ev["note"], "感知事件,判断权在局长"
    e.turn()  # 局长收到后回了空拍(=选择继续等)
    assert not e.turn_ready(), "单发不循环:空拍后不再催"


def test_hooks_mean_game_running_no_fire(tmp_path):
    e = _engine(tmp_path)
    e.turn()
    e._last_activity = time.time() - IDLE_CHECK_S - 5
    e.state.timers.append(time.time() + 300)   # 挂着未到点的计时器=游戏在跑
    assert not e.turn_ready()
    assert not any(x.get("type") == "idle_check" for x in e.event_queue)
    e.state.timers.clear()
    e.state.open_ask = {"prompt": "谁赢?", "asked": "全场", "options": None,
                        "deadline": None, "answers": {}, "window": 10}
    assert not e.turn_ready(), "问询开着=游戏在跑,不算死锁"


def test_player_action_resets_sentinel(tmp_path):
    e = _engine(tmp_path)
    e.turn()
    e._last_activity = time.time() - IDLE_CHECK_S - 5
    e.push_event({"type": "done", "player": "甲"})   # 玩家一动:场上活着
    assert not any(x.get("type") == "idle_check" for x in e.event_queue)
    e.turn()  # 消费 done
    assert not e.turn_ready(), "动作把静默钟归零,哨兵不响"


def test_finished_never_fires(tmp_path):
    e = _engine(tmp_path)
    e.turn()
    e.state.finished = True
    e._last_activity = time.time() - IDLE_CHECK_S - 5
    assert not e.turn_ready()
