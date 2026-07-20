"""复盘统计:episode 流水 → 门槛验收单对表页(每局一页,零 API 纯本地)。

口径对齐两条既有裁定:
- 情绪不可测,只报可数事实(笑点分布/出声率/三信号计数),不做情绪臆测;
- 好不好玩由房主复盘拍板,本脚本只把仪表摆出来。

用法:python tools/episode_report.py outputs/episodes/sim_xxx.jsonl [...]
      python tools/episode_report.py outputs/episodes/c6/   (目录=逐个出)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def analyze(path: Path) -> dict:
    turns, silents = [], 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if line.get("episode_summary"):
            continue
        if line.get("host_silent"):
            silents += 1
            continue
        turns.append(line)

    laugh_by_turn: Counter = Counter()
    speak_turns: dict[str, set] = {}
    players: list[str] = []
    sig = Counter()          # forfeit / optout(pass 并入)/ done
    draw = {"calls": 0, "ok": 0}
    music: list[str] = []
    private = Counter()      # 自己看 / 额头
    clamps = 0
    asks = {"count": 0, "silent_hits": 0}
    t_ms: list[int] = []

    for i, line in enumerate(turns):
        players = line.get("digest", {}).get("scores", {}).keys() or players
        for e in line.get("events_in", []):
            t = e.get("type")
            if "t_ms" in e:
                t_ms.append(e["t_ms"])
            if t == "laugh":
                laugh_by_turn[i] += 1
            if t in ("forfeit", "optout", "pass", "done"):
                sig["optout" if t == "pass" else t] += 1
            if t == "ask_result":
                asks["count"] += 1
                asks["silent_hits"] += len(e.get("silent", []))
            if e.get("player") and t in ("say", "vote", "tap", "done", "forfeit",
                                         "ritual_done", "laugh"):
                speak_turns.setdefault(e["player"], set()).add(i)
        for c, r in zip(line.get("tool_use", []), line.get("results", [])):
            name = c.get("name", "")
            ok = bool(r.get("ok"))
            if not ok:
                clamps += 1
            if name == "draw_atom":
                draw["calls"] += 1
                draw["ok"] += ok
            if name == "music.play" and ok:
                music.append((r.get("result") or {}).get("playing", "?"))
            if name == "show" and ok:
                vis = (r.get("result") or {}).get("visibility")
                if vis in ("自己看", "额头"):
                    private[vis] += 1

    n = len(turns) or 1
    total_laugh = sum(laugh_by_turn.values())
    top2 = sum(v for _, v in laugh_by_turn.most_common(2))
    dur_min = (max(t_ms) - min(t_ms)) / 60000 if len(t_ms) >= 2 else 0.0
    return {
        "file": path.name, "turns": len(turns), "host_silent_beats": silents,
        "duration_min": round(dur_min, 1),
        "laugh_total": total_laugh,
        # 爆点集中度:六桌复盘经验——笑声该集中在爆点而非均匀铺开
        "laugh_top2_share": round(top2 / total_laugh, 2) if total_laugh else None,
        "speak_rate": {p: round(len(s) / n, 2) for p, s in sorted(speak_turns.items())},
        "signals": dict(sig),
        "draw_atom": {**draw, "per_turn": round(draw["ok"] / n, 3)},
        "clamps": clamps,
        "music_plays": music,
        "private_shows": dict(private),
        "asks": asks,
    }


def render(st: dict) -> str:
    lines = [f"## {st['file']} —— {st['turns']} 拍 / {st['duration_min']} 分钟"
             f"(主持沉默拍 {st['host_silent_beats']})", ""]
    lines.append(f"- 笑声:{st['laugh_total']} 次,前两爆点占比 "
                 f"{st['laugh_top2_share'] if st['laugh_top2_share'] is not None else '—'}"
                 "(高=集中引爆,低=均匀/没爆点)")
    lines.append(f"- 三信号:{st['signals'] or '零'}(optout 含 pass;零 optout 零 forfeit 是好局参考线)")
    lines.append(f"- 弹药库:draw {st['draw_atom']['calls']} 次 / 成 {st['draw_atom']['ok']} 条"
                 f" / {st['draw_atom']['per_turn']} 条每拍(第一轮战损参考线 0.017,疗效线 0.13)")
    lines.append(f"- 出声率:{st['speak_rate']}(8 人桌观察带 0.18–0.35,低于 0.1 = 观众化)")
    lines.append(f"- 私发:{st['private_shows'] or '无'};钳制 {st['clamps']} 次;"
                 f"问询 {st['asks']['count']} 次(被挤未答 {st['asks']['silent_hits']} 人次)")
    if st["music_plays"]:
        lines.append(f"- DJ:换歌 {len(st['music_plays'])} 次 → {' → '.join(st['music_plays'])}")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    files: list[Path] = []
    for a in args:
        p = Path(a)
        files += sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    for f in files:
        print(render(analyze(f)))


if __name__ == "__main__":
    main()
