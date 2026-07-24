"""狂野模式 M1 · 权威状态机(黑板)。

账本铁律:一切事实只经 state 工具写入;模型只发意图,执行在这里。
出处:docs/records/狂野模式-运行时调用协议v0.md、狂野模式-架构立案草案.md L0。
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any


# 派活时效:超过这么久没人交活、也没有新的派活,就当这活已经散了(口头玩法在桌上
# 早翻篇了)。到期后按钮回到中性——宁可不认领,也不许挂着一条过期的"这是 X 的活"。
ASSIGN_TTL_S = 240

# 分数流水在手机上停留多久。够一眼看见(轮询 900ms,十来秒足够跨过一次抬头),
# 又不至于让上一轮的账压在这一轮的头上。
SCORE_FLASH_S = 14


@dataclass
class SkillGrant:
    """权力型惩罚:可持有的道具原语(绑实物/限次数/限本局)。"""

    prop: str
    holder: str
    bound_object: str  # 现场实物;空串=虚拟态
    uses_left: int
    ritual: str  # 使用条件仪式(动作+台词,做不全发动失败)


@dataclass
class GameState:
    players: list[str]
    wildness_cap: int  # 野度档(开局口味钳制)
    time_budget_min: int  # 房主裁定:开局必问"想玩多久"
    scene_objects: list[str] = field(default_factory=list)  # perceive 扫描结果(M1 手填)
    score_style: str = "清账"  # 结算风格口味:清账(即时结算)| 综艺(可攒分可颁奖);负向标签两档都禁
    scores: dict[str, int] = field(default_factory=dict)
    round_no: int = 0
    focus: str | None = None
    # 派活信号(真机病历 2026-07-24:「完成」按钮的「(X的活)」标签挂在 focus 上,而
    # focus 是局长手动维护的字段——64 拍里只调过 2 次,定向问询却发生了 5 次。前半场
    # 完全没拦、后半场标签滞后十几拍、该锁时不锁)。焦点归焦点(局长嘴里的主角),
    # 派活归派活:谁手上有活由**引擎从真实调用里自动记**,不劳局长自觉。
    # {"players":[...], "why": 一句人话, "src": 工具名, "at": epoch}
    assigned: dict | None = None
    # 分数流水(真机病历 2026-07-24:「中途分数变动无实时提示」——账本只有一个当前值,
    # 手机上那个数字自己变了没人知道为什么;认罚自动扣的那 1 分尤其冤,主持有时还漏播)。
    # 三个改账的口子(认罚自动扣 / state.add_score / state.settle 清账)全部走 score(),
    # 每一笔留 {player, delta, why, at},手机拿最近 SCORE_FLASH_S 秒的弹条播报。
    score_log: list[dict] = field(default_factory=list)
    atoms_used: list[str] = field(default_factory=list)
    grants: list[SkillGrant] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    timers: list[float] = field(default_factory=list)  # 活动计时器到期时刻(epoch)
    host_perception: str = "转写"  # 感知档:按钮=真机基线(只知道谁按了什么);转写=开发台上帝视角仪器
    open_ask: dict | None = None  # 进行中的限时问询:{prompt, options, deadline, answers}
    playlist: list[str] = field(default_factory=list)  # 房主上传歌单(真人可写、AI 只读只调)
    now_playing: str | None = None  # 当前曲目(music 工具唯一写入口)
    # 快枪手对决(手机原生旗舰件):{players, draw_at, taps}。拔枪时点系统保密——
    # 主持不知道(回执也不给),玩家端只看到 drawn 布尔翻面,公平由系统毫秒判定。
    duel: dict | None = None
    # 系统级炸铃(喊停类玩法:传花停/木头人/数到停):{at: 到期epoch, fx: 文案}。
    # 判定时刻是那声"停!",局长没嗓子、文字停没人看见(玩家在抬头玩)——像快枪手
    # draw_at 一样由系统精确执行,时刻一到全桌手机毫秒级齐响(App 用 server_now 算钟差
    # 本地定时,消灭轮询抖动),LLM 不在回路。同一时刻只挂一个铃,新铃覆盖旧铃;
    # timer 正常到期逻辑不变(照发 timer_expired 叫醒主持)。铃是公开广播,人人要响。
    pending_bell: dict | None = None
    # 读场输入(房主开局一句话+可选场景速写):没有它,"自动读场"无场可读,
    # 局局跑成通用娱乐局。进 system prompt(每局静态),不进逐拍 digest。
    occasion: str = ""      # 局型/场合:生日/团建/情侣/陌生人破冰/老友重聚……
    scene_brief: str = ""   # 场景速写(手填或将来开局一拍照的视觉摘要)
    # 拍照/录音判定(多模态判定通道):主持显式发起的判定时刻,非常驻监听。
    pending_photo: dict | None = None  # {player, prompt}
    pending_audio: dict | None = None  # {player, prompt}(语调打分/口令复述等,接音频口即通)
    # 私件挂账:发出去的额头牌/私发任务(只记 holder+档,不记内容——digest 是半公开面)。
    # 实测:额头牌发完就沉底,"从头到尾没有被使用或提及规则"。
    private_out: list = field(default_factory=list)
    # 骰盅道具(prop.dice_cup):{player: {kind:"骰盅", count:int, rolled: None|[点数],
    # challenged_by?: str, bid?: {count,face}|None}}。
    # 局长只发盅(公开可见:桌上都知道谁有盅),点数由玩家自己在 App 上摇——
    # 玩的动作留在玩家手里(房主原则:局长不替玩家玩)。每人同时最多一只盅,
    # 重复发=换新盅重置(rolled 归 None)。点数不进 digest 公共面,只走本人私件+局长对账信道。
    # 开牌(玩家拍「开牌!」按钮,type=challenge):全桌盅立 challenged_by/bid 标并锁定
    # 不可再摇(点数即证据);一局一开,解锁靠局长清算后 prop.cancel/重发(标随盅清)。
    props: dict = field(default_factory=dict)
    # 额头牌状态化(房主裁定 2026-07-23):藏信息是**长在人身上的道具**,不是私件短信。
    # {player: 牌面内容}——App 端"点这个玩家看他的牌",本人视图里自己那张永远拿不到。
    # 私件流水里仍留 👀 行(向后兼容),但正解渲染走这里。
    foreheads: dict = field(default_factory=dict)
    # 私发内容全面道具化(房主裁定 2026-07-23):卧底词/情侣密令/毒杯号不是"私信文本",
    # 是有类型、有生命周期的**牌**。show(自己看)自由文本口是文字流游戏的最后后门,
    # 用类型化的 prop.card 取而代之。{player: [{kind, content, dealt_turn, status}]}——
    # 一人可持多张;kind ∈ 词卡|密令卡|号码卡;status: held(持牌)→used(用过)|revealed(翻公开)。
    # 发卡动作公开可见(桌上知道谁收到一张什么类型的牌,内容不可见);牌面内容只走本人
    # 私件(🎴 前缀)+荷官回执给局长对账,不进 digest/别人的 view;revealed 时内容进公开面。
    cards: dict = field(default_factory=dict)
    settled: dict[str, int] = field(default_factory=dict)  # 已清账累计口数(清账制的另一半)
    discards: list[dict] = field(default_factory=list)  # 主动弃牌留痕:弃牌≠用牌
    finished: bool = False

    def __post_init__(self) -> None:
        for p in self.players:
            self.scores.setdefault(p, 0)

    # —— 派活账(谁手上有活)——
    def score(self, player: str, delta: int, why: str) -> int:
        """改账的唯一入口:动数字的同时留一笔流水,手机才有得播报。

        直接写 scores[p] 的老写法一律改走这里——账面变了却说不出为什么,
        在桌上等于没变(玩家看不见,只会觉得分数在自己乱跳)。
        """
        if player not in self.scores or not delta:
            return self.scores.get(player, 0)
        self.scores[player] += delta
        self.score_log.append({"player": player, "delta": int(delta),
                               "why": why, "at": _time.time()})
        del self.score_log[:-40]   # 只留个尾巴,流水不是账本
        return self.scores[player]

    def score_flash(self) -> list[dict]:
        """最近这几秒的分数变动(全桌可见:账本本来就是公开的)。"""
        now = _time.time()
        return [{"player": e["player"], "delta": e["delta"], "why": e["why"]}
                for e in self.score_log if now - e["at"] <= SCORE_FLASH_S]

    def assign(self, players: list[str], why: str, src: str) -> dict | None:
        """记一笔派活。后写覆盖先写:最近一次定向动作就是"现在轮到谁"。"""
        ps = [p for p in players if p in self.players]
        if not ps:
            return None
        self.assigned = {"players": ps, "why": why, "src": src, "at": _time.time()}
        return self.assigned

    def unassign(self, src: str | None = None) -> None:
        """收活。src 给定时只收这个来源的(问询收窗不该顺手把对决的活也抹了)。"""
        if self.assigned and (src is None or self.assigned.get("src") == src):
            self.assigned = None

    def hand_in(self, player: str) -> None:
        """某人交活:把他从派活名单里划掉,名单空了这笔活就结了。"""
        a = self.assigned
        if not a or player not in a["players"]:
            return
        rest = [p for p in a["players"] if p != player]
        self.assigned = {**a, "players": rest} if rest else None

    def actors(self) -> list[str]:
        """现在这活是谁的——过期的派活不算数(见 ASSIGN_TTL_S)。空=引擎不知道,
        此时任何人按「完成」都不算认错人(口头玩法本来就没有派活信号)。"""
        a = self.assigned
        if not a or _time.time() - a.get("at", 0) > ASSIGN_TTL_S:
            return []
        return list(a["players"])

    def digest(self, time_left_min: float) -> dict[str, Any]:
        """上行状态摘要(协议 §五 state_digest)。"""
        return {
            "round": self.round_no,
            "scores": dict(self.scores),
            "focus": self.focus,
            # 派活账(引擎自动记的,不是你手写的 focus):谁手上有活、因为什么。
            # 你按这个判「谁交活」就不会认错人;想改就派新活(ask/judge/duel/
            # draw_atom(for_player)/state.set_focus),别在心里改。
            "assigned": ({"players": a["players"], "why": a["why"]}
                         if (a := self.assigned) and self.actors() else None),
            "atoms_used": list(self.atoms_used),
            "grants": [
                {"prop": g.prop, "holder": g.holder, "uses_left": g.uses_left}
                for g in self.grants
            ],
            "scene_objects": list(self.scene_objects),
            "time_left_min": round(time_left_min, 1),
            "timer_running": bool(self.timers),
            "野度档": self.wildness_cap,
            "now_playing": self.now_playing,
            "duel": ({"vs": list(self.duel["players"]),
                      "drawn": _time.time() >= self.duel["draw_at"]}
                     if self.duel else None),
            "photo_wait": self.pending_photo["player"] if self.pending_photo else None,
            "audio_wait": self.pending_audio["player"] if self.pending_audio else None,
            "private_out": list(self.private_out),
            # 骰盅挂账(公共面):谁有盅、几颗、摇没摇——点数不进这里(只走本人私件+局长对账信道),
            # 不然 digest 是半公开面,点数一进来大话骰就没得吹了。
            # 开牌标(challenged_by)公开挂上(桌上拍桌喊的,不泄密)——challenge 事件只出现
            # 一拍,主持后续拍凭这里知道这一口还没清算完。
            "dice_cups": [{"player": p, "count": pr.get("count"),
                           "rolled": pr.get("rolled") is not None,
                           **({"challenged_by": pr["challenged_by"]}
                              if pr.get("challenged_by") else {})}
                          for p, pr in self.props.items()],
            # 牌卡挂账(公共面):谁持几张什么类型的牌、什么状态——牌面**内容一律不进**
            # 这里(digest 是半公开面,进来就等于当众翻牌)。revealed 的牌内容走公开
            # 回合行(全场公开 display)+玩家 view 的 table_cards,不靠 digest 带。
            "cards": [{"player": p, "kind": c["kind"], "status": c["status"]}
                      for p, cs in self.cards.items() for c in cs],
        }


class ClampError(Exception):
    """钳制层拒写(代码拦,不靠模型自觉)。"""
