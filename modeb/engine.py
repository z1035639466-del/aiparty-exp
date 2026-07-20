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
        ask = self.state.open_ask
        if ask and ev.get("player") and ev.get("type") in ("vote", "say", "tap") \
                and (ask["deadline"] is None or time.time() <= ask["deadline"]):
            # 应答归到这次问询名下,一人一票、后答覆盖先答。
            ask["answers"][ev["player"]] = ev.get("value") or ev.get("text") or "确认"
            if ask["deadline"] is None:
                # 计时从「第一个人应声」才开始,给后到的人留窗口。
                # 没人应声就不计时——安静等着,不催。
                ask["deadline"] = time.time() + ask["window"]
                self.state.timers.append(ask["deadline"])
            ev = dict(ev, _absorbed=True)  # 已被问询吸收,不单独叫醒主持
        self.event_queue.append(ev)

    def _close_ask(self) -> dict | None:
        """窗口到点:按多数认,一票也认,平票取先到,没人答就明说没人答。"""
        ask = self.state.open_ask
        # deadline 为 None = 还没人应声。安静等着,不催、不结算、不叫醒主持。
        if not ask or ask["deadline"] is None or time.time() < ask["deadline"]:
            return None
        self.state.open_ask = None
        tally: dict[str, int] = {}
        for v in ask["answers"].values():
            tally[v] = tally.get(v, 0) + 1
        winner = max(tally, key=lambda k: tally[k])
        return {"type": "ask_result", "prompt": ask["prompt"], "tally": tally,
                "winner": winner, "answers": dict(ask["answers"]),
                "note": "按多数认,一票也认"}

    def time_left_min(self) -> float:
        return self.state.time_budget_min - (time.time() - self._t0) / 60.0

    def _perceive(self, events: list[dict]) -> list[dict]:
        """按感知档裁剪送给主持的事件——决定这局在多接近真机的条件下被测。

        转写档:全文送达(需要 ASR 落地才成立,当前模拟台默认)。
        按钮档:只送结构化按压,say 降级成「有人说话」,内容听不见。
        """
        mode = getattr(self.state, "host_perception", "转写")
        if mode != "按钮":
            return events
        out = []
        for e in events:
            if e.get("type") == "say":
                out.append({"type": "say", "player": e.get("player"), "inaudible": True})
            else:
                out.append(e)
        return out

    def turn_ready(self) -> bool:
        """事件驱动心跳:开局首拍 / 有新事件 / 有计时器到点,才该叫醒主持。
        房主裁定(2026-07-18):没回应就等——桌上没动静不打扰,主持不必编进展。"""
        # 被问询吸收的应答不单独叫醒主持——等窗口收完一起给它,免得它半途插话。
        if self.marks["turns"] == 0 or any(not e.get("_absorbed") for e in self.event_queue):
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
        closed = self._close_ask()
        if closed:
            events.append(closed)
        # 现场的局长是半瞎半聋的:听不见桌上的自由交谈,只知道谁按了什么。
        # 模拟台默认给全文,会把主持的本桌化改造能力测得虚高——真机上它拿不到这些。
        decision = self.driver.decide(digest, self._perceive(events))
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
