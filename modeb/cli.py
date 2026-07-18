"""M1 CLI:单机跑通一局,episode 完整落盘。

用法:python -m modeb.cli [--players 疯子明,小静,大鹏] [--minutes 30] [--wildness 6]
     [--objects 瓶子,冰块,纸巾,手机,杯子,打火机] [--out outputs/episodes]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .driver_scripted import ScriptedDriver
from .engine import Engine
from .state import GameState


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", default="疯子明,小静,大鹏")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--wildness", type=int, default=6)
    ap.add_argument("--objects", default="瓶子,冰块,纸巾,手机,杯子,打火机")
    ap.add_argument("--out", default="outputs/episodes")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    state = GameState(
        players=args.players.split(","),
        wildness_cap=args.wildness,
        time_budget_min=args.minutes,
        scene_objects=args.objects.split(","),
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    episode = Path(args.out) / f"episode_{stamp}.jsonl"
    engine = Engine(state, ScriptedDriver(), episode, rng_seed=args.seed)

    # M1 用脚本事件流模拟玩家端;M2 起由六机模拟台注入
    feed = {
        3: [{"type": "vote_result", "pass": True, "tally": "2:1"}, {"type": "laugh"}],
        5: [{"type": "ritual_done", "prop": "决斗手套"}, {"type": "laugh"}, {"type": "laugh"}],
        6: [{"type": "pass", "player": state.players[2]}],
        8: [{"type": "laugh"}],
    }
    while not state.finished:
        for ev in feed.get(engine.marks["turns"], []):
            engine.push_event(ev)
        engine.turn()
        if engine.marks["turns"] >= 60:
            break
    summary_line = json.loads(episode.read_text(encoding="utf-8").splitlines()[-1]) \
        if engine.marks["turns"] >= 60 else None
    # run() 已在 finished 后由 turn 循环替代;补写 summary
    if not summary_line or not summary_line.get("episode_summary"):
        summary = engine.run(max_turns=engine.marks["turns"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"episode → {episode}")


if __name__ == "__main__":
    main()
