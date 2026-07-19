"""M1 回合循环:事件聚合 → 驱动器决策 → 钳制执行 → episode 落盘。

协议 v0 §四:规则内事件本地零 API;决策点聚合一次驱动器回合。
埋点四信号(裁定纪要 §③,自第一行代码起带上):laugh / skip / duration / replay。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from .state import GameState
from .tools import ToolExecutor

MAX_TOOLS_PER_TURN = 2
MAX_SENTENCES = 3


class Driver(Protocol):
    """决策器接口:活局长(LLM)与脚本驱动同签名——插座,不是居民。"""

    def decide(self, digest: dict, events: list[dict]) -> dict: ...


class Engine:
    def __init__(self, state: GameState, driver: Driver, episode_path: Path, rng_seed: int = 0) -> None:
        self.state = state
        self.driver = driver
        self.tools = ToolExecutor(state, rng_seed)
        self.episode_path = episode_path
        self.event_queue: list[dict] = []
        self.marks = {"laugh_events": 0, "skips": 0, "forfeits": 0, "turns": 0}
        self._t0 = time.time()
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        self._ep = episode_path.open("w", encoding="utf-8")

    # —— 玩家端/观察员事件入队(两回合之间聚合,不逐条打驱动器) ——
    def push_event(self, ev: dict) -> None:
        ev.setdefault("t_ms", int(time.time() * 1000))  # 竞速判定公平依据(快枪手等)
        if ev.get("type") == "laugh":
            self.marks["laugh_events"] += 1
        if ev.get("type") in ("pass", "optout"):  # 安全退出(零代价底线,罕用):立即生效
            self.marks["skips"] += 1
        if ev.get("type") == "forfeit":  # 认罚跳过(日常的「过」):正常游戏动作,按赌注结算
            self.marks["forfeits"] += 1
        self.event_queue.append(ev)

    def time_left_min(self) -> float:
        return self.state.time_budget_min - (time.time() - self._t0) / 60.0

    def turn_ready(self) -> bool:
        """事件驱动心跳:开局首拍 / 有新事件 / 有计时器到点,才该叫醒主持。
        房主裁定(2026-07-18):没回应就等——桌上没动静不打扰,主持不必编进展。"""
        if self.marks["turns"] == 0 or self.event_queue:
            return True
        now = time.time()
        return any(t <= now for t in self.state.timers)

    # —— 一个决策回合 ——
    def turn(self) -> dict:
        now = time.time()
        fired = [t for t in self.state.timers if t <= now]
        self.state.timers = [t for t in self.state.timers if t > now]
        digest = self.state.digest(self.time_left_min())
        events, self.event_queue = self.event_queue, []
        events += [{"type": "timer_expired"} for _ in fired]
        decision = self.driver.decide(digest, events)
        text = decision.get("text", "")
        calls = decision.get("tool_use", [])[:MAX_TOOLS_PER_TURN]
        overflow = len(decision.get("tool_use", [])) - len(calls)
        sentence_count = sum(text.count(p) for p in "。!?!?") or (1 if text else 0)
        scores_before = dict(self.state.scores)
        results = [self.tools.execute(c) for c in calls]
        line = {
            "turn": self.marks["turns"], "digest": digest, "events_in": events,
            "text": text, "tool_use": calls, "results": results,
            "ledger_diff": {p: self.state.scores[p] - scores_before[p]
                            for p in self.state.scores if self.state.scores[p] != scores_before[p]},
            "warnings": ([f"tool_use 超限截断 {overflow} 个"] if overflow > 0 else [])
                        + ([f"主持词 {sentence_count} 句超 {MAX_SENTENCES}"] if sentence_count > MAX_SENTENCES else []),
        }
        self._ep.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.marks["turns"] += 1
        return line

    # —— 跑完一局 ——
    def run(self, max_turns: int = 60) -> dict:
        while not self.state.finished and self.marks["turns"] < max_turns:
            self.turn()
        summary = {
            "episode_summary": True,
            "turns": self.marks["turns"],
            "duration_min": round((time.time() - self._t0) / 60.0, 2),
            "laugh_events": self.marks["laugh_events"],
            "skips": self.marks["skips"],
            "would_replay_yes": None,  # 局后填,不得凭回忆批量补录(试点纪律沿用)
            "final_scores": dict(self.state.scores),
            "clamps": self.tools.clamp_log,
        }
        self._ep.write(json.dumps(summary, ensure_ascii=False) + "\n")
        self._ep.close()
        return summary
