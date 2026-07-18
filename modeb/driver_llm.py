"""LLM 驱动器接口件(M1 只定协议,不接线;接线属 M2)。

上行三段(协议 v0 §二):system(人格+铁律+野度+名册,每局固定吃缓存)/
tools 声明 / 本回合消息(digest+events+佐料)。下行:text ≤3 句 + tool_use ≤2。
"""
from __future__ import annotations

from pathlib import Path

TOOLS_DECLARATION = [
    {"name": "show", "desc": "向玩家端展示内容", "args": {"content": "str", "visibility": "自己看|额头|全场公开"}},
    {"name": "ask", "desc": "向玩家提问/发起确认", "args": {"player": "str|全场", "prompt": "str", "options": "list?"}},
    {"name": "random.pick", "desc": "公平随机选择", "args": {"from": "players|list", "exclude": "list?"}},
    {"name": "random.int", "desc": "公平随机整数", "args": {"min": "int", "max": "int"}},
    {"name": "timer", "desc": "计时", "args": {"seconds": "int", "label": "str"}},
    {"name": "state.add_score", "desc": "写分(账本唯一入口,钳制 |delta|<=3)", "args": {"player": "str", "delta": "int", "reason": "str"}},
    {"name": "state.set_focus", "desc": "设焦点人物", "args": {"player": "str"}},
    {"name": "state.next_round", "desc": "进下一轮", "args": {}},
    {"name": "state.use_grant", "desc": "消耗一次已持有技能", "args": {"prop": "str", "holder": "str"}},
    {"name": "state.finish", "desc": "收局", "args": {}},
    {"name": "fx", "desc": "音效/特效", "args": {"effect": "str"}},
    {"name": "draw_atom", "desc": "从弹药库抽原子(分面过滤+排已用)", "args": {"atom_type": "str?", "野度": "int?", "exclude": "list?", "grant_to": "str?"}},
]


def build_system_prompt(players: list[str], wildness_cap: int, time_budget_min: int) -> str:
    persona = Path("docs/records/狂野模式-活局长prompt-v0.md")
    persona_text = persona.read_text(encoding="utf-8") if persona.exists() else ""
    return (
        f"{persona_text}\n\n"
        f"【本桌】玩家:{'、'.join(players)};野度档:{wildness_cap};时长预算:{time_budget_min}分钟。\n"
        "【铁律】每回合最多3句话+2个工具调用;分数只经 state 工具;任何人说「过」立刻短路当前环节;"
        "你发出的只是意图,越界调用会被钳制层拒写并留痕。"
    )


class LLMDriver:
    """M2 接线:把 digest+events 组装为 messages,调用 API,解析 text+tool_use。"""

    def __init__(self) -> None:
        raise NotImplementedError("LLM 驱动器属 M2;M1 用 ScriptedDriver 验收引擎。")
