"""桌友 agent(模拟台/burn-in 专用假人,非产品面——产品桌上座位是真人)。

架构定位:与玩家 pane 完全同接口——只会 engine.push_event 认识的事件,
不碰账本不碰工具。房主占真人座,其余座位由便宜模型(haiku/deepseek)扮演。
"""
from __future__ import annotations

import json
import re

ALLOWED_EVENTS = {"laugh", "pass", "tap", "vote", "ritual_done", "say"}
MAX_EVENTS_PER_TURN = 2

PLAYER_CONTRACT = (
    "你每回合只输出一个 JSON 对象:"
    '{"events": [{"type": "laugh|pass|tap|vote|ritual_done|say", ...}]}'
    ";vote 带 value(赞成/反对),say 带 text(≤1句);最多 2 个事件,可以为空;"
    "JSON 之外不写任何字。"
)


def build_player_system(name: str, persona: str, interests: list[str]) -> str:
    return (
        f"你是派对上的玩家「{name}」。人设:{persona}。兴趣画像:{'、'.join(interests) or '无'}。\n"
        "每回合你会看到主持人刚说的话、工具结果与桌面摘要。像真人一样**节制**反应:"
        "被点名/被挑战才积极回应(用 say/tap/ritual_done);内容好笑才 laugh;"
        "不想参与可喊 pass(有代价的场合慎用);表决时用 vote。平淡回合就输出空 events。\n"
        f"{PLAYER_CONTRACT}"
    )


def parse_player_events(raw: str, name: str) -> list[dict]:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    events = []
    for ev in (obj.get("events") or [])[:MAX_EVENTS_PER_TURN]:
        if isinstance(ev, dict) and ev.get("type") in ALLOWED_EVENTS:
            ev = dict(ev)
            ev["player"] = name  # 座位身份由系统钉死,模型不得冒名
            events.append(ev)
    return events


class LLMPlayerAgent:
    def __init__(self, name: str, persona: str, transport,
                 interests: list[str] | None = None) -> None:
        self.name = name
        self.transport = transport
        self.system = build_player_system(name, persona, interests or [])

    def react(self, turn_line: dict, digest: dict) -> list[dict]:
        msg = json.dumps({
            "host_text": turn_line.get("text", ""),
            "tool_results": [r for r in turn_line.get("results", []) if r.get("ok")],
            "digest": {"round": digest.get("round"), "focus": digest.get("focus"),
                       "scores": digest.get("scores")},
            "you": self.name,
        }, ensure_ascii=False)
        try:
            raw = self.transport.complete(self.system, [{"role": "user", "content": msg}])
        except Exception:
            return []  # 桌友掉线不卡局
        return parse_player_events(raw, self.name)


class ScriptedPlayerAgent:
    """测试假人:按预置队列出事件。"""

    def __init__(self, name: str, script: list[list[dict]]) -> None:
        self.name = name
        self.script = list(script)

    def react(self, turn_line: dict, digest: dict) -> list[dict]:
        events = self.script.pop(0) if self.script else []
        return [dict(ev, player=self.name) for ev in events]
