"""七件工具 + 钳制层。模型只发意图;这里是唯一执行者。

钳制层拦在工具调用与账本之间(架构案 L2):分数越界拒写、未知玩家拒写、
野度/S 位过滤、「过」字短路。拒写不是异常流,是正常留痕事件。
"""
from __future__ import annotations

import random as _random
from typing import Any

from .atoms_seed import SEED_ATOMS
from .state import ClampError, GameState, SkillGrant

MAX_SCORE_DELTA = 3  # 单次写分钳制


class ToolExecutor:
    def __init__(self, state: GameState, rng_seed: int = 0) -> None:
        self.state = state
        self.rng = _random.Random(rng_seed)
        self.clamp_log: list[dict] = []

    # —— 分发 ——
    def execute(self, call: dict[str, Any]) -> dict[str, Any]:
        name, args = call["name"], call.get("input", {})
        try:
            handler = getattr(self, "_t_" + name.split(".")[0])
        except AttributeError:
            return self._clamp(name, args, "未知工具(白名单外)")
        try:
            result = handler(name, args)
            return {"tool": name, "ok": True, "result": result}
        except ClampError as e:
            return self._clamp(name, args, str(e))

    def _clamp(self, name: str, args: dict, reason: str) -> dict:
        entry = {"tool": name, "ok": False, "clamped": reason, "args": args}
        self.clamp_log.append(entry)
        return entry

    # —— show / fx / timer / ask:M1 CLI 仅落 episode,UI 指令由客户端消费 ——
    def _t_show(self, name: str, a: dict) -> dict:
        return {"display": a.get("content", ""), "visibility": a.get("visibility", "全场公开")}

    def _t_fx(self, name: str, a: dict) -> dict:
        return {"fx": a.get("effect", "")}

    def _t_timer(self, name: str, a: dict) -> dict:
        secs = int(a.get("seconds", 10))
        if not 1 <= secs <= 600:
            raise ClampError(f"timer 秒数越界: {secs}")
        return {"timer_started": secs, "label": a.get("label", "")}

    def _t_ask(self, name: str, a: dict) -> dict:
        # M1:提问下发即返回;回答由事件队列(玩家端)带回下一回合
        return {"asked": a.get("player", "全场"), "prompt": a.get("prompt", ""), "options": a.get("options")}

    # —— random ——
    def _t_random(self, name: str, a: dict) -> dict:
        if name == "random.pick":
            pool = a.get("from")
            items = list(self.state.players) if pool == "players" else list(pool or [])
            for ex in a.get("exclude", []):
                if ex in items:
                    items.remove(ex)
            if not items:
                raise ClampError("random.pick 空池")
            return {"picked": self.rng.choice(items)}
        if name == "random.int":
            lo, hi = int(a.get("min", 1)), int(a.get("max", 6))
            return {"value": self.rng.randint(lo, hi)}
        raise ClampError(f"random 未知子操作: {name}")

    # —— state:账本唯一入口 ——
    def _t_state(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else a.get("op", "")
        if op == "add_score":
            player, delta = a.get("player"), int(a.get("delta", 0))
            if player not in self.state.scores:
                raise ClampError(f"未知玩家: {player}")
            if abs(delta) > MAX_SCORE_DELTA:
                raise ClampError(f"写分越界 |{delta}|>{MAX_SCORE_DELTA}")
            self.state.scores[player] += delta
            return {"scores": dict(self.state.scores), "reason": a.get("reason", "")}
        if op == "set_focus":
            player = a.get("player")
            if player is not None and player not in self.state.players:
                raise ClampError(f"未知玩家: {player}")
            self.state.focus = player
            return {"focus": player}
        if op == "next_round":
            self.state.round_no += 1
            return {"round": self.state.round_no}
        if op == "use_grant":
            prop = a.get("prop")
            for g in self.state.grants:
                if g.prop == prop and g.holder == a.get("holder"):
                    if g.uses_left <= 0:
                        raise ClampError(f"{prop} 次数已尽")
                    g.uses_left -= 1
                    return {"prop": prop, "uses_left": g.uses_left}
            raise ClampError(f"未持有技能: {a.get('holder')}/{prop}")
        if op == "note":
            self.state.notes[a.get("key", "_")] = a.get("value")
            return {"noted": a.get("key", "_")}
        if op == "finish":
            self.state.finished = True
            return {"finished": True}
        raise ClampError(f"state 未知子操作: {op}")

    # —— draw_atom:接口先定库后换(M1=种子数组;M3 换 atoms.sqlite,签名不变) ——
    def _t_draw_atom(self, name: str, a: dict) -> dict:
        pool = []
        for atom in SEED_ATOMS:
            if atom["id"] in self.state.atoms_used or atom["id"] in a.get("exclude", []):
                continue
            if atom["wildness"] > min(self.state.wildness_cap, int(a.get("野度", 10))):
                continue
            if atom["safety"] and a.get("exclude_safety", True) and set(atom["safety"]) & {"逼量嫌疑"}:
                continue
            if a.get("atom_type") and atom["type"] != a["atom_type"]:
                continue
            if atom["props"] and not (set(atom["props"]) & set(self.state.scene_objects)):
                continue  # 实体门槛:现场没有所需实物则不抽(通用桌具按在场清单动态放行)
            pool.append(atom)
        if not pool:
            raise ClampError("draw_atom 无可用原子(检查野度/道具/排除项)")
        atom = self.rng.choice(pool)
        self.state.atoms_used.append(atom["id"])
        if "skill" in atom:  # 技能授予型:自动登记权力(绑实物取现场匹配第一件)
            bound = next((o for o in atom["props"] if o in self.state.scene_objects), "")
            holder = a.get("grant_to") or self.state.focus or self.state.players[0]
            self.state.grants.append(SkillGrant(
                prop=atom["skill"]["prop"], holder=holder, bound_object=bound,
                uses_left=atom["skill"]["uses"], ritual=atom["skill"]["ritual"]))
        return {"atom": {k: atom[k] for k in ("id", "name", "type", "text", "wildness", "currency")},
                "bound_object": next((o for o in atom["props"] if o in self.state.scene_objects), None)}
