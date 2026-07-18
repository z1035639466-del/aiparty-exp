"""整桌编排:主持驱动器 + 桌友 agent 群 + 真人座位。

回合节拍:engine.turn()(主持决策+执行)→ 各桌友 agent 对本回合反应 →
事件入队 → 下一回合聚合。真人座位不在此层——真人经模拟台 pane / 产品端注入。
CLI 为全 bot burn-in(便宜模型烧机找问题);真人加入走 simulator。
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .driver_llm import LLMDriver
from .engine import Engine
from .state import GameState
from .transports import make_transport


class TableRunner:
    def __init__(self, engine: Engine, bots: list) -> None:
        self.engine = engine
        self.bots = bots  # 具 react(turn_line, digest) 接口

    def run_turn(self) -> dict:
        line = self.engine.turn()
        digest = self.engine.state.digest(self.engine.time_left_min())
        for bot in self.bots:
            for ev in bot.react(line, digest):
                self.engine.push_event(ev)
        return line

    def run_to_finish(self, max_turns: int = 60) -> dict:
        while not self.engine.state.finished and self.engine.marks["turns"] < max_turns:
            self.run_turn()
        return self.engine.run(max_turns=self.engine.marks["turns"])


DEFAULT_PERSONAS = ["显眼包,什么都敢接", "冷面吐槽王,笑点极高", "气氛组组长,逢梗必笑",
                    "社恐但护短", "卷王,输不起但嘴硬", "老好人,谁罚都帮着求情"]


def main() -> None:
    ap = argparse.ArgumentParser(description="全 bot burn-in(真人加入请用 simulator)")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "deepseek"])
    ap.add_argument("--host-model", default="sonnet")
    ap.add_argument("--seat-model", default="haiku")
    ap.add_argument("--players", default="疯子明,小静,大鹏")
    ap.add_argument("--minutes", type=int, default=15)
    ap.add_argument("--wildness", type=int, default=6)
    ap.add_argument("--objects", default="瓶子,冰块,纸巾,手机,杯子")
    ap.add_argument("--out", default="outputs/episodes")
    args = ap.parse_args()

    from .player_agent import LLMPlayerAgent
    players = args.players.split(",")
    state = GameState(players=players, wildness_cap=args.wildness,
                      time_budget_min=args.minutes, scene_objects=args.objects.split(","))
    host = LLMDriver(make_transport(args.provider, args.host_model),
                     players, args.wildness, args.minutes)
    bots = [LLMPlayerAgent(p, DEFAULT_PERSONAS[i % len(DEFAULT_PERSONAS)],
                           make_transport(args.provider, args.seat_model))
            for i, p in enumerate(players)]
    ep = Path(args.out) / f"table_{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    runner = TableRunner(Engine(state, host, ep), bots)
    summary = runner.run_to_finish()
    print(f"episode → {ep}")
    print(summary)


if __name__ == "__main__":
    main()
