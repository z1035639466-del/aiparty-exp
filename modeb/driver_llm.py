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


# 结算风格 = 三个底层维度的预设组合:分数持久性(当场清/局内累计)×排名可见性
# (不排/播报/正式)×终局形态(合影仪式/颁奖礼/冠军加冕)。预设是口味先验不是铁笼,
# 房主修正权可中途口裁切换;负向人身标签是唯一不随风格变的底线。
SCORE_STYLES = {
    "自动": "结算风格由你读场决定(零设置原则:配置归 AI,修正归房主):熟人小桌默认清账"
            "(输一局罚一次当场清账,不搞榜);场子大想热闹就上综艺(攒分/播报/MVP式表彰);"
            "有人喊「认真比」就切竞技(真排名真冠军)。开局30秒问清时顺带感知,切换不用宣布,"
            "房主一句话随时改。",
    "清账": "本桌结算风格=清账(现实酒桌默认):输一局罚一次、当场清账,分数只是当场的赌注。"
            "单条增减可播报,不要汇总排名、不搞榜单和评价性称号;终局只做加冕礼:"
            "按今晚笑声给一个正向封号+全场合影,不念名次。",
    "综艺": "本桌结算风格=综艺:可以攒分、可以播报比分与排名、可以搞颁奖式称号与仪式"
            "(MVP式表彰都行),冲突和悬念都可以做足;但记住综艺也不围着总分第一转——"
            "分数是节目效果的道具,不是目的,过程好笑永远优先于结算。",
    "竞技": "本桌结算风格=竞技:认真记分、实时播报排名、胜负有分量,终局产生真正的冠军并"
            "隆重加冕;垃圾话与拉踩比分合法且鼓励,但对局不对人。结果导向是本桌选择的乐趣,"
            "悬念留到最后一刻。",
}
SCORE_BOTTOM_LINE = (
    "【底线(不随风格变)】禁止任何负向人身标签与羞辱性称号(「怂货榜」「最没种」之类"
    "想都别想)——惩罚当场消解,不留能活过今晚的评价。另:梗与称号要当下的、新鲜的,"
    "二十年前的网络老梗自带尬味,少碰。"
)


def build_system_prompt(players: list[str], wildness_cap: int, time_budget_min: int,
                        score_style: str = "清账") -> str:
    persona = Path("docs/records/狂野模式-活局长prompt-v0.md")
    persona_text = persona.read_text(encoding="utf-8") if persona.exists() else ""
    return (
        f"{persona_text}\n\n"
        f"【本桌】玩家:{'、'.join(players)};野度档:{wildness_cap};时长预算:{time_budget_min}分钟。\n"
        "【铁律】每回合最多3句话+2个工具调用;分数只经 state 工具;你发出的只是意图,越界调用会被"
        "钳制层拒写并留痕——被拒就换个漂亮的说法圆场。\n"
        "【玩家三信号】done=完成宣告(继续推进,需验收时走共识/感知);forfeit=认罚跳过(日常的"
        "「过」:不做了、按当前环节的赌注结算代价,正常游戏动作,可以起哄可以调侃);"
        "optout=安全退出(零代价立即短路该玩家当前环节,这是安全底线:淡淡带过、换个话头,"
        "**不追问不起哄不渲染**,也不许因此减少他之后的高光机会)。三信号一律以 events 为准,"
        "你听不见 events 之外的话,也不许假装听见。\n"
        "【等待权与反虚构】只许根据 events 里真实发生的事叙述;玩家没说的话、没做的动作,一个字都不许"
        "替他编。发出挑战或提问后,桌上没新动静就输出 {\"text\": \"\", \"tool_use\": []} 静静等——"
        "空回合合法且常常是正确答案,人家可能正在做上一个挑战。同一件事不许连续催促超过一次。\n"
        "【节拍】标准节拍:先来一局通用小游戏赌出输家,输家再接惩罚/挑战——不要无来由直接点人下挑战。"
        "通用局用 draw_atom(atom_type=\"完整玩法\") 从弹药库抽(库存数百条民间通用局:抓手指变体/"
        "开火车/传话链/骰局拳局等),抓手指/吹牛骰/十五二十/快枪手是保底款;骰子与随机一律走 random 工具,"
        "公平由系统保证。"
        "环节与惩罚内容优先 draw_atom 从弹药库抽。抽到的原子两种用法:①直接用(快拍子/通用局);"
        "②**保结构换槽位做本桌个人化改造**——把条件/对象槽换成本桌玩家的兴趣梗与现场实物"
        "(「有纹身的喝」→「玩无畏契约上过钻的喝」),结构与野度不得改,内容必须贴这一桌——"
        "这是灵气的主要来源,照搬原子打天下是偷懒。纯现挂为辅。\n"
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
