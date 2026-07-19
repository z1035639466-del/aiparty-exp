"""LLM 驱动器(M2 第一单):消息组装/解析/容错实装,传输层可插拔。

协议 v0:上行三段(system 固定吃缓存 / tools 声明 / 本回合 digest+events+佐料);
下行 text ≤3 句 + tool_use ≤2。模型只回意图,执行权在 ToolExecutor。
真实接线:实现 Transport.complete(调 Anthropic API,流式可选)即通;
本仓测试用 MockTransport,不依赖网络与密钥。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

MAX_TOOLS_PER_TURN = 2
HISTORY_WINDOW = 6  # 保留最近 N 回合主持词,维持口风连续

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

OUTPUT_CONTRACT = (
    "你每回合只输出一个 JSON 对象,格式:"
    '{"text": "≤3句主持词", "tool_use": [{"name": "工具名", "input": {...}}]}'
    ";tool_use 最多 2 个,只许用声明过的工具;JSON 之外不写任何字。"
)


SCORE_STYLES = {
    "清账": "本桌结算风格=清账(现实酒桌默认):输一局罚一次、当场清账,分数只是当场的赌注。"
            "单条增减可播报,不要汇总排名、不搞榜单和评价性称号;终局只做加冕礼:"
            "按今晚笑声给一个正向封号+全场合影,不念名次。",
    "综艺": "本桌结算风格=综艺:可以攒分、可以播报比分与排名、可以搞颁奖式称号与仪式,"
            "冲突和悬念都可以做足;但记住综艺也不围着总分第一转——分数是节目效果的道具,"
            "不是目的,过程好笑永远优先于结算。",
}
SCORE_BOTTOM_LINE = (
    "【底线(不随风格变)】禁止任何负向人身标签与羞辱性称号(「怂货榜」「最没种」之类"
    "想都别想)——惩罚当场消解,不留能活过今晚的评价。"
)


def build_system_prompt(players: list[str], wildness_cap: int, time_budget_min: int,
                        score_style: str = "清账") -> str:
    persona = Path("docs/records/狂野模式-活局长prompt-v0.md")
    persona_text = persona.read_text(encoding="utf-8") if persona.exists() else ""
    return (
        f"{persona_text}\n\n"
        f"【本桌】玩家:{'、'.join(players)};野度档:{wildness_cap};时长预算:{time_budget_min}分钟。\n"
        "【铁律】每回合最多3句话+2个工具调用;分数只经 state 工具;任何人说「过」立刻短路当前环节,"
        "不追问不起哄;你发出的只是意图,越界调用会被钳制层拒写并留痕——被拒就换个漂亮的说法圆场。\n"
        "【等待权与反虚构】只许根据 events 里真实发生的事叙述;玩家没说的话、没做的动作,一个字都不许"
        "替他编。发出挑战或提问后,桌上没新动静就输出 {\"text\": \"\", \"tool_use\": []} 静静等——"
        "空回合合法且常常是正确答案,人家可能正在做上一个挑战。同一件事不许连续催促超过一次。\n"
        "【节拍】标准节拍:先用 draw_atom 抽一局通用小游戏(吹牛骰/十五二十/石头剪刀布,骰子与随机"
        "一律走 random 工具,公平由系统保证)赌出输家,输家再接惩罚/挑战——不要无来由直接点人下挑战。"
        "环节与惩罚内容优先 draw_atom 从弹药库抽,现挂为辅。\n"
        f"【记分观】{SCORE_STYLES.get(score_style, SCORE_STYLES['清账'])}\n"
        f"{SCORE_BOTTOM_LINE}\n"
        f"【输出契约】{OUTPUT_CONTRACT}\n"
        f"【工具】{json.dumps(TOOLS_DECLARATION, ensure_ascii=False)}"
    )


class Transport(Protocol):
    """传输层:接真实 API 时实现本方法(流式与否由实现决定)。"""

    def complete(self, system: str, messages: list[dict]) -> str: ...


def parse_decision(raw: str) -> dict | None:
    """从模型原文里抠出决策 JSON;抠不出返回 None(上层容错)。"""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("text", ""))
    calls = obj.get("tool_use", [])
    if not isinstance(calls, list):
        calls = []
    cleaned = []
    for c in calls[:MAX_TOOLS_PER_TURN]:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            cleaned.append({"name": c["name"], "input": c.get("input", {}) or {}})
    return {"text": text, "tool_use": cleaned}


FALLBACK = {"text": "局长走神了一秒——罚自己一口,咱们继续!", "tool_use": []}


class LLMDriver:
    """与 ScriptedDriver 同签名:decide(digest, events) -> {text, tool_use}。"""

    def __init__(self, transport: Transport, players: list[str],
                 wildness_cap: int, time_budget_min: int, max_retries: int = 1,
                 score_style: str = "清账") -> None:
        self.transport = transport
        self.system = build_system_prompt(players, wildness_cap, time_budget_min, score_style)
        self.history: list[dict] = []  # [{"role": "assistant"|"user", "content": str}]
        self.max_retries = max_retries
        self.malformed_count = 0

    def _turn_message(self, digest: dict, events: list[dict]) -> str:
        return json.dumps({"state_digest": digest, "events": events}, ensure_ascii=False)

    def decide(self, digest: dict, events: list[dict]) -> dict:
        user_msg = {"role": "user", "content": self._turn_message(digest, events)}
        messages = self.history[-HISTORY_WINDOW * 2:] + [user_msg]
        decision = None
        for _ in range(1 + self.max_retries):
            raw = self.transport.complete(self.system, messages)
            decision = parse_decision(raw)
            if decision is not None:
                break
            self.malformed_count += 1
        if decision is None:
            decision = dict(FALLBACK)
        self.history.append(user_msg)
        self.history.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
        return decision


class MockTransport:
    """测试用:按预置剧本回原文(含坏格式样本),不碰网络。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict]) -> str:
        self.calls.append({"system": system, "messages": messages})
        return self.responses.pop(0) if self.responses else json.dumps(FALLBACK, ensure_ascii=False)
