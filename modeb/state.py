"""狂野模式 M1 · 权威状态机(黑板)。

账本铁律:一切事实只经 state 工具写入;模型只发意图,执行在这里。
出处:docs/records/狂野模式-运行时调用协议v0.md、狂野模式-架构立案草案.md L0。
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any


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
    settled: dict[str, int] = field(default_factory=dict)  # 已清账累计口数(清账制的另一半)
    discards: list[dict] = field(default_factory=list)  # 主动弃牌留痕:弃牌≠用牌
    finished: bool = False

    def __post_init__(self) -> None:
        for p in self.players:
            self.scores.setdefault(p, 0)

    def digest(self, time_left_min: float) -> dict[str, Any]:
        """上行状态摘要(协议 §五 state_digest)。"""
        return {
            "round": self.round_no,
            "scores": dict(self.scores),
            "focus": self.focus,
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
        }


class ClampError(Exception):
    """钳制层拒写(代码拦,不靠模型自觉)。"""
