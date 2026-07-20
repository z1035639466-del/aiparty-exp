"""桌友 agent(模拟台/burn-in 专用假人,非产品面——产品桌上座位是真人)。

架构定位:与玩家 pane 完全同接口——只会 engine.push_event 认识的事件,
不碰账本不碰工具。房主占真人座,其余座位由便宜模型(haiku/deepseek)扮演。
"""
from __future__ import annotations

import json
import re
import sys

ALLOWED_EVENTS = {"laugh", "pass", "optout", "forfeit", "done", "tap", "vote", "ritual_done", "say"}
MAX_EVENTS_PER_TURN = 2

PLAYER_CONTRACT = (
    "你每回合只输出一个 JSON 对象:"
    '{"events": [{"type": "laugh|pass|tap|vote|ritual_done|say", ...}]}'
    ";vote 带 value(赞成/反对),say 带 text(≤1句)与 to"
    "(桌上=对桌友说,默认;局长=定向对主持说,问规则/申诉/答问询才用);"
    "最多 2 个事件,可以为空;JSON 之外不写任何字。"
)


def build_player_system(name: str, persona: str, interests: list[str]) -> str:
    return (
        f"你是派对上的玩家「{name}」。人设:{persona}。兴趣画像:{'、'.join(interests) or '无'}。\n"
        "每回合你会看到主持人刚说的话、工具结果与桌面摘要。像真人一样**节制**反应:"
        "被点名/被挑战才积极回应(用 say/tap/ritual_done);起哄调侃对桌友说(to=桌上),"
        "问规则、申诉、答主持问询才对局长说(to=局长)——别拿局长当聊天对象;内容好笑才 laugh;"
        "做完挑战报 done;不想做就 forfeit 认罚跳过;optout 是零代价安全退出、只在真不舒服时用;表决时用 vote。平淡回合就输出空 events。\n"
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
            if ev.get("type") == "say" and ev.get("to") not in ("局长", "桌上"):
                ev.pop("to", None)  # 乱写的去向按默认(桌上)处理
            events.append(ev)
    return events


class LLMPlayerAgent:
    def __init__(self, name: str, persona: str, transport,
                 interests: list[str] | None = None) -> None:
        self.name = name
        self.transport = transport
        self.system = build_player_system(name, persona, interests or [])
        self.errors = 0          # 累计失败次数,供模拟台在座位上打 ⚠️
        self.last_error = ""     # 最近一次失败原因,首次即打到 stderr

    def react(self, turn_line: dict, digest: dict, inbox: list | None = None) -> list[dict]:
        msg = json.dumps({
            "host_text": turn_line.get("text", ""),
            "你的私密收件(仅你可见,别念出来)": inbox or [],
            "tool_results": [r for r in turn_line.get("results", []) if r.get("ok")],
            "digest": {"round": digest.get("round"), "focus": digest.get("focus"),
                       "scores": digest.get("scores")},
            "you": self.name,
        }, ensure_ascii=False)
        try:
            raw = self.transport.complete(self.system, [{"role": "user", "content": msg}])
        except Exception as e:
            # 掉线不卡局——但"不卡局"不等于"不告诉任何人"。静默降级会让
            # key 错/模型串过期表现为「这帮 bot 怎么这么闷」,查错方向全跑偏。
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"
            if self.errors == 1:  # 只吼第一次,免得每 4 秒刷屏
                print(f"⚠️ 桌友「{self.name}」调用失败(后续同类错误不再重复播报):"
                      f"{self.last_error}", file=sys.stderr, flush=True)
            return []
        return parse_player_events(raw, self.name)


class ScriptedPlayerAgent:
    """测试假人:按预置队列出事件。"""

    def __init__(self, name: str, script: list[list[dict]]) -> None:
        self.name = name
        self.script = list(script)

    def react(self, turn_line: dict, digest: dict, inbox: list | None = None) -> list[dict]:
        events = self.script.pop(0) if self.script else []
        return [dict(ev, player=self.name) for ev in events]
