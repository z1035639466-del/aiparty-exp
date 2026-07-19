"""狂野模式 M1 · 权威状态机(黑板)。

账本铁律:一切事实只经 state 工具写入;模型只发意图,执行在这里。
出处:docs/records/狂野模式-运行时调用协议v0.md、狂野模式-架构立案草案.md L0。
"""
from __future__ import annotations

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
    scores: dict[str, int] = field(default_factory=dict)
    round_no: int = 0
    focus: str | None = None
    atoms_used: list[str] = field(default_factory=list)
    grants: list[SkillGrant] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    timers: list[float] = field(default_factory=list)  # 活动计时器到期时刻(epoch)
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
        }


class ClampError(Exception):
    """钳制层拒写(代码拦,不靠模型自觉)。"""
