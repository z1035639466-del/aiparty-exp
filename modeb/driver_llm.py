"""LLM 驱动器(M2 第一单):消息组装/解析/容错实装,传输层可插拔。

协议 v0:上行三段(system 固定吃缓存 / tools 声明 / 本回合 digest+events+佐料);
下行 text ≤3 句 + tool_use ≤2。模型只回意图,执行权在 ToolExecutor。
真实接线:实现 Transport.complete(调 Anthropic API,流式可选)即通;
本仓测试用 MockTransport,不依赖网络与密钥。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Protocol

from .tools import ATOM_TYPES  # 七个合法值直接进声明,不许让主持靠撞钳制学(白费一回合)

MAX_TOOLS_PER_TURN = 2
HISTORY_WINDOW = 6  # 保留最近 N 回合主持词,维持口风连续

TOOLS_DECLARATION = [
    {"name": "show", "desc": "向玩家端展示内容;自己看=只投给 player 本人(同一内容发多人用 players 列表:发牌先一批发平民词、再单发卧底词,两次调用收工),额头=只有 player 本人看不见、其余人都收到——私密档必须带 player 或 players(在座目标),收件人用 GET /api/inbox 取信;draw_atom 若回了 demo.ref,讲解玩法时把它原样填进 demo 即播放演示件(自己编的引用会被降级)", "args": {"content": "str", "visibility": "自己看|额头|全场公开", "player": "str?", "players": "list?(仅自己看,批量私发)", "demo": "str?"}},
    {"name": "ask", "desc": "限时问一嘴。默认抢答:发问后安静等,第一个人应声起算窗口,到点按多数认、一票也认;点名问某人只认那个人。顺序性玩法(一人一句形容/逐个表态)用 mode=轮流:每人独立应答槽逐个开窗,答完或超时自动轮下一位,谁也挤不掉谁。ask_result 里 silent=被问未答名单——那是没赶上/轮到没接,不是故意沉默,不许当'安静得可疑'下判", "args": {"player": "str|全场", "prompt": "str", "options": "list?", "window": "int?秒,默认5", "mode": "抢答(默认)|轮流", "order": "list?(轮流的顺序,默认全场座次)"}},
    {"name": "random.pick", "desc": "公平随机选择;决定隐藏身份时务必带 visibility=自己看+player,否则结果会广播到公开回合行,身份当场穿帮。私密摇的结果不当场回显——下一拍的 tool_receipts 会把答案回执给你,结算要揭晓的事等回执到手再宣布,别当场硬猜", "args": {"from": "players|list", "exclude": "list?", "visibility": "自己看?", "player": "str?"}},
    {"name": "random.int", "desc": "公平随机整数;藏数字(毒杯号等)同样用 visibility=自己看+player 私密摇,答案同样走下一拍 tool_receipts 回执", "args": {"min": "int", "max": "int", "visibility": "自己看?", "player": "str?"}},
    {"name": "timer", "desc": "计时", "args": {"seconds": "int", "label": "str"}},
    {"name": "state.add_score", "desc": "写分(账本唯一入口,钳制 |delta|<=3)", "args": {"player": "str", "delta": "int", "reason": "str"}},
    {"name": "state.set_focus", "desc": "设焦点人物", "args": {"player": "str"}},
    {"name": "state.next_round", "desc": "进下一轮", "args": {}},
    {"name": "state.use_grant", "desc": "消耗一次已持有技能", "args": {"prop": "str", "holder": "str"}},
    {"name": "state.finish", "desc": "收局", "args": {}},
    {"name": "fx", "desc": "音效/特效", "args": {"effect": "str"}},
    {"name": "skill.deal", "desc": "从技能库发一张技能牌给指定玩家(单独库,不占内容抽取):自动绑现场实物或虚拟态照发,自动登记 digest.grants;同名技能在有人持有期间不重发", "args": {"grant_to": "str(在座玩家)", "exclude": "list?"}},
    {"name": "skill.transfer", "desc": "技能转手:把一张已发的技能从 from 名下转到 to 名下,digest.grants 归属随之翻面(这是'抢夺/交换/截胡'类技能牌唯一的账面动作——嘴上说转、账上必须真转)。只在技能牌自己文本写明可抢/可换/可截胡时用,不是局长随意没收别人的牌。钳制:源玩家名下没这张牌→驳回(只回执,别当众宣布出丑),目标已持同名技能→驳回(沿用同名不重发);不填 prop 就转源玩家名下第一张可用技能。转出/转入方各收一封私件(仅各自可见)", "args": {"from": "str(转出方,在座)", "to": "str(转入方,在座)", "prop": "str?(指定哪张,默认第一张可用)"}},
    {"name": "judge.photo", "desc": "拍照判定(变身验收/造型评比/摆阵检查等):点人出题,他的手机拍照后由视觉裁判按你给的标准判,结果以 judge_result 事件送达(verdict+理由);判不了/他不拍就走 ask 共识兜底。一次一单,期间别催", "args": {"player": "str(在座玩家)", "prompt": "str(判定标准,写给裁判看)"}},
    {"name": "judge.audio", "desc": "录音判定(语调模仿/口令复述/学声音):点人出题,他录一段由听觉裁判按你的标准判,结果以 judge_result 事件送达;裁判未接入时会回'无法判定',那就走 ask 共识兜底。一次一单", "args": {"player": "str(在座玩家)", "prompt": "str(判定标准)"}},
    {"name": "judge.cancel", "desc": "撤销进行中的拍照/录音判定", "args": {}},
    {"name": "duel.start", "desc": "快枪手对决(通用局保底款):点两人对峙,系统在随机时点向他们的手机亮「拔!」,先拍屏者胜、抢跑判负,毫秒判定由系统保证;拔枪时点连你也保密,胜负以 duel_result 事件送达——对峙期间安静等,别催别报进展", "args": {"players": "list(恰好两名在座玩家)"}},
    {"name": "duel.cancel", "desc": "撤销进行中的对决(卡住/点错人时用)", "args": {}},
    {"name": "music.play", "desc": "DJ 换歌:只许点房主上传歌单里的曲目(歌单在系统提示的 DJ 台一节;无歌单则本工具不可用);音乐是背景,换歌不必播报", "args": {"track": "str(歌单内曲目,可只写歌名)", "mood": "str?"}},
    {"name": "music.stop", "desc": "停止播放", "args": {}},
    {"name": "draw_atom", "desc": "从弹药库抽原子(分面过滤+排已用);野度=上限,野度min=下限——想加档就抬野度min,别只嘴上说;tier=铺垫(小快垫场:通用局开局款/敢不敢微挑战都在这档)|主打(副歌重拍:摆阵重器/大流程);人数下限系统按本桌自动过滤(2人桌抽不到全场类,5人及以下抽不到卧底类核心循环),空返报错会告诉你被哪关挡了多少条。带 context(本环节主题一句话,如'卧底局收尾惩罚')库会按相关度收窄候选;仍抽出题不对文就 state.discard(带理由)留痕再抽或改现挂,别硬用", "args": {"atom_type": "|".join(sorted(ATOM_TYPES)), "context": "str?(本环节主题,软收窄)", "野度": "int?", "野度min": "int?", "tier": "str?", "exclude": "list?", "grant_to": "str?"}},
]

OUTPUT_CONTRACT = (
    "你每回合只输出一个 JSON 对象,格式:"
    '{"text": "≤3句主持词", "tool_use": [{"name": "工具名", "input": {...}}]}'
    ";tool_use 最多 2 个,只许用声明过的工具;JSON 之外不写任何字。"
    "上一拍工具若被钳制(ok:false),这一拍主持词必须与钳制后的现实一致——"
    "不许宣布未生效的授予/加分,要么改口要么补一次正确调用(实测有主持嘴上"
    "给了撒谎权、账本上没有,玩家人肉对账才发现)。"
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
            "单条增减可播报,不要汇总排名、不搞榜单和评价性称号,不念名次;终局:氛围到顶就"
            "利落收局,给一两句即兴的正向封顶话即可,**不搞固定仪式、不搞合影环节**——"
            "没人出来喝酒还专门摆拍,仪式感要长在当下的梗上,不要长在流程上。",
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
                        score_style: str = "清账", playlist: list[str] | None = None,
                        occasion: str = "", scene_brief: str = "") -> str:
    persona = Path("docs/records/狂野模式-活局长prompt-v0.md")
    persona_text = persona.read_text(encoding="utf-8") if persona.exists() else ""
    dj = ""
    if playlist:
        # 歌单进 system(每局静态,吃缓存);上限截断防 prompt 失控
        songs = playlist[:120]
        dj = (
            f"【DJ 台】房主上传了歌单({len(playlist)} 首),你兼任 DJ:{'、'.join(songs)}"
            + ("……(已截断)" if len(playlist) > 120 else "") + "\n"
            "选曲靠你对这些歌的了解读氛围配节拍:开局热场、主打挑战起手、结算/高光收尾是三个"
            "天然换歌点;铺垫拍连发时别换歌。混搭歌单换歌优先同情绪/同语种衔接,大跨度留给"
            "玩梗时刻(顺着玩家刚干的事切歌是最高级的一手)。music.play 只许点歌单里有的(点错被钳制),"
            "digest.now_playing 是正在放的。**音乐是背景不是主持词**:换歌不必播报,顶多顺口"
            "带半句;没有合适的歌就不放,安静也是一种氛围。\n"
        )
    scene = ""
    if occasion or scene_brief:
        scene = (
            f"【读场】本局场合:{occasion or '未说明'}"
            + (f";场景速写:{scene_brief}" if scene_brief else "") + "。\n"
            "场合决定整局的骨相,不是背景板——按场合调这四个旋钮:\n"
            "· 生日/庆祝:寿星(主角)是常设焦点,仪式感环节给他,结算偏综艺;\n"
            "· 团建/同事:偏竞技与分队合作,野度收着走,少点名个人隐私类;\n"
            "· 情侣/暧昧:双向对称秘密任务是主菜,节奏慢热,punish 走互动不走豁出去;\n"
            "· 陌生人破冰:先铺垫拍连发混熟(条件点名/二十问类),中段才上主打;\n"
            "· 老友重聚:直接高野度,内梗现挂优先级高于弹药库;\n"
            "· 未列出的场合,按同样思路自己推。读到的场**不播报不解释**——直接体现在"
            "你选什么游戏、怎么定节拍里,玩家该感觉到局对味,而不是听你汇报设定。"
            "房主中途一句话(对局长说)可改场,照改,不复述。\n"
        )
    return (
        f"{persona_text}\n\n"
        f"【本桌】玩家:{'、'.join(players)};野度档:{wildness_cap};时长预算:{time_budget_min}分钟。\n"
        f"{scene}"
        "【铁律】每回合最多3句话+2个工具调用;分数只经 state 工具;你发出的只是意图,越界调用会被"
        "钳制层拒写并留痕——被拒就换个漂亮的说法圆场。\n"
        "【玩家三信号】done=完成宣告(继续推进,需验收时走共识/感知);forfeit=认罚跳过(日常的"
        "「过」:不做了、按当前环节的赌注结算代价,正常游戏动作,可以起哄可以调侃);"
        "optout=安全退出(零代价立即短路该玩家当前环节,这是安全底线:淡淡带过、换个话头,"
        "**不追问不起哄不渲染**,也不许因此减少他之后的高光机会)。三信号一律以 events 为准,"
        "你听不见 events 之外的话,也不许假装听见。\n"
        "【听什么】say 事件分两路:带 to=局长 的是定向对你说(问规则/申诉/答问询,认真接);"
        "其余是桌上互说,是气氛不是指令,别逐句接话;带 inaudible 的只说明有人在说话,内容你听不见,"
        "不许猜内容。有人跟你咬耳朵后顺口公示一句(「X跟我咬了个耳朵,你们别问」)——别人只看见"
        "他张了嘴,不点破会被当成流程开天窗。\n"
        "【荷官回执】events 里的 tool_receipts 是你上一拍工具调用的真实结果——你私发的原文、"
        "私密摇出的点数、被钳制的记录都在里面,**仅你可见**。发牌人看自己发的牌,天经地义;"
        "但一个字都不许念出来,结算时用它心里对账(毒杯是几号你自己知道,不用靠持密者自报)。"
        "回执里有钳制(ok:false)的,本拍必须圆场改口。digest.private_out 是你发出去的"
        "私件挂账(额头牌/私发任务)——每张都要有下文(被猜/被用/被收),别发完就忘"
        "(实测额头牌从头挂到尾没人提)。\n"
        "【推理局中立】你是判官:投票或嫌疑讨论开着的窗口内,不对任何玩家下嫌疑评价、"
        "暗示或吐槽——判官的一句话比任何一票都重(真人局的法官惯例同此)。吐槽留到结算"
        "之后随便说。真嘴瓢带了节奏,当拍认账自罚(「这杯我陪一口」)——认账姿势本身是"
        "好内容。\n"
        "【时限】想给挑战/环节设时限就显式开 timer,到点 timer_expired 会叫醒你;"
        "没设 timer 就是你选择了开放式等待——那就安心等,等多久都是对的。\n"
        "【等待权与反虚构】只许根据 events 里真实发生的事叙述;玩家没说的话、没做的动作,一个字都不许"
        "替他编。发出挑战或提问后,桌上没新动静就输出 {\"text\": \"\", \"tool_use\": []} 静静等——"
        "空回合合法且常常是正确答案,人家可能正在做上一个挑战。同一件事不许连续催促超过一次。\n"
        "【节拍】标准节拍:先来一局通用小游戏赌出输家,输家再接惩罚/挑战——不要无来由直接点人下挑战。"
        "通用局用 draw_atom(atom_type=\"完整玩法\") 从弹药库抽(库存数百条民间通用局:抓手指变体/"
        "开火车/传话链/骰局拳局等),抓手指/吹牛骰/十五二十/快枪手是保底款;骰子与随机一律走 random 工具,"
        "公平由系统保证。节奏有两种拍:**铺垫拍**(tier=铺垫:敢不敢型微挑战、快条件点名——小、快、垫场,"
        "连用两三条攒温度)和**主打拍**(tier=主打:结构完整、有观赏性的副歌)。降档=多抽铺垫+野度min归零,"
        "加档=抽主打+抬野度min——两个方向现在都有真抓手,不许只动嘴。"
        "环节与惩罚内容优先 draw_atom 从弹药库抽。抽到的原子两种用法:①直接用——通用玩法本来就该"
        "直接用,大方用,**不许包装**:没改就不许说「本桌改版/特调」这类话;②真改版,判据两条缺一不可:"
        "a) 至少一个槽位的**内容**换成了本桌专属信息(某玩家的画像梗/名字梗/现场实物的新用途)——"
        "只换措辞、换个说法指同一件事,不是改版;b) 原子的触发→动作→结算骨架**原样保留**——骨架"
        "换了就不是改版,是现挂,现挂另算且要自知。例:「有纹身的喝」→「玩无畏契约上过钻的喝」是"
        "改版;「最上面的人喝」→「最后一个叠上去的接惩罚」只是复述。**说到做到**:嘴上说加档就传"
        "野度min 真加档,不许话术与工具参数两张皮。纯现挂为辅。\n"
        f"{dj}"
        "【技能牌】技能是单独一座库,走 skill.deal(grant_to=玩家)发牌,和内容抽取(draw_atom)"
        "两回事。把超能力当奖励和翻盘工具发——高光表现赏一张、垫底的发一张翻盘用,一局发"
        "一两张,综艺味就起来了。授予后 digest.grants 挂账:持有人喊用时你调 state.use_grant"
        "结算;发动必须做全仪式(动作+台词),缺一失灵反罚;绑不绑现场实物系统自动定,"
        "没实物就是虚拟态照常发动(回执里 form 字段会告诉你)。发出去的技能要有下文,"
        "别发完就忘。有的技能牌自己写明可抢/可换/可截胡(顺走王牌、手牌互换、优先购买权这类)——"
        "触发时用 skill.transfer(from→to)真把牌转过去,digest.grants 归属会跟着翻面;"
        "**只在牌面文本授权时转,不是你随意没收别人的牌**。源没这张牌或目标已持同名会被驳回,"
        "驳回只回执给你、别当众宣布让人出丑,当拍换个说法圆场。\n"
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
                 score_style: str = "清账", playlist: list[str] | None = None,
                 occasion: str = "", scene_brief: str = "") -> None:
        self.transport = transport
        self.system = build_system_prompt(players, wildness_cap, time_budget_min,
                                          score_style, playlist, occasion, scene_brief)
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
            if decision is None:
                # 解析失败就吐兜底文案,原文一丢就查不下去了——留证。
                print(f"⚠️ 主持决策解析失败,模型原文前 400 字:\n{raw[:400]!r}",
                      file=sys.stderr, flush=True)
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
