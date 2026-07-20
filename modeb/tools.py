"""七件工具 + 钳制层。模型只发意图;这里是唯一执行者。

钳制层拦在工具调用与账本之间(架构案 L2):分数越界拒写、未知玩家拒写、
野度过滤、「过」字短路。拒写不是异常流,是正常留痕事件。
"""
from __future__ import annotations

import random as _random
import re
from typing import Any

from .atoms_seed import SEED_ATOMS
from .state import ClampError, GameState, SkillGrant

MAX_SCORE_DELTA = 3  # 单次写分钳制

ATOM_TYPES = {"完整玩法", "条件点名", "任务内容", "道具挑战", "问答题目", "规则修饰", "技能授予"}

# 安全闸已拆(房主裁定 2026-07-20):只组织不监督——外发/强灌本来就监督不了,
# 不想玩的人 forfeit/optout 自己过就完事,引擎侧不替现场做道德把关。
# safety_flags 仍随数据存留(语料层照旧打标),只是不再参与抽取过滤。
ATOMS_FILE = "inputs/atoms/atoms-v1.jsonl"  # M-int-1 抽取产物;存在即自动并入弹药库
PATTERNS_FILE = "inputs/patterns/patterns-v0.jsonl"  # 模式卡(手工首批;M-int-2 聚类产物原地并入)


def load_atom_pool(atoms_path: str | None = None) -> list[dict]:
    """种子佐料 + 抽取库(confidence=high 且非涉嫌逼量者)合并;库文件缺席时纯种子。"""
    import json
    from pathlib import Path as _P
    pool = list(SEED_ATOMS)
    seen = {a["id"] for a in pool}
    path = _P(atoms_path or ATOMS_FILE)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except json.JSONDecodeError:
                continue
            if a.get("confidence") != "high" or a.get("atom_id") in seen:
                continue
            seen.add(a["atom_id"])
            pool.append({
                "id": a["atom_id"], "name": a.get("name", a["atom_id"]),
                "type": a.get("atom_type", "任务内容"),
                "text": a.get("text_clean") or a.get("text_raw", ""),
                "wildness": int(a.get("wildness", 3)), "props": a.get("props_explicit", []),
                "safety": a.get("safety_flags", []), "currency": a.get("currency", "表演"),
            })
    for atom in pool:  # 价值分档:铺垫(敢不敢型微挑战/超短条件点名,垫场拍)|主打(副歌)
        if "tier" not in atom:
            t = atom.get("text", "")
            atom["tier"] = "铺垫" if (re.search(r"不敢的|敢不敢", t)
                                      or (atom.get("type") == "条件点名" and len(t) <= 18)) else "主打"
    return pool


def load_pattern_cards(path: str | None = None) -> list[dict]:
    """模式卡:一簇近重复原子共享的玩法骨架。演示资产挂在这里,不挂原子——
    原子说"要什么东西",模式说"怎么做动作",demo_ref 属于"怎么做"。"""
    import json
    from pathlib import Path as _P
    p = _P(path or PATTERNS_FILE)
    if not p.exists():
        return []
    cards = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        if c.get("pattern_id") and c.get("variants"):
            cards.append(c)
    return cards


class ToolExecutor:
    def __init__(self, state: GameState, rng_seed: int = 0,
                 atoms_path: str | None = None, patterns_path: str | None = None) -> None:
        self.state = state
        self.rng = _random.Random(rng_seed)
        self.clamp_log: list[dict] = []
        self.atom_pool = load_atom_pool(atoms_path)
        self.pattern_cards = load_pattern_cards(patterns_path)
        # 原子 → 模式卡(变体清单是唯一挂载点,不在原子文件上加字段——text_raw 层不动)
        self.pattern_by_atom = {aid: c for c in self.pattern_cards for aid in c["variants"]}
        self.known_demo_refs = {c["demo_ref"] for c in self.pattern_cards if c.get("demo_ref")}

    # —— 分发 ——
    def execute(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call["name"]
        # 形状归一:契约要求 {"name":..., "input":{...}},但模型常把参数平铺到顶层
        # ({"name":"ask","player":"全场","prompt":"..."})。此时 call.get("input") 是 None,
        # 参数会被整包丢掉——工具照样"执行成功"却带着空参数,比报错更难查。
        args = call.get("input")
        if not isinstance(args, dict):
            args = {k: v for k, v in call.items() if k not in ("name", "input")}
        # 模型爱把不填的参数显式写成 null,而 a.get(k, 默认值) 在「键在、值为 null」
        # 时返回 None 而非默认值,下游 int()/for 就地爆炸。入口剥掉 null,让默认值生效。
        args = {k: v for k, v in args.items() if v is not None}
        try:
            handler = getattr(self, "_t_" + name.split(".")[0])
        except AttributeError:
            return self._clamp(name, args, "未知工具(白名单外)")
        try:
            result = handler(name, args)
            return {"tool": name, "ok": True, "result": result}
        except ClampError as e:
            return self._clamp(name, args, str(e))
        except (TypeError, ValueError, KeyError) as e:
            # 参数形状不对不该掀桌:降级成钳制记录,局照转,证据留在 episode 里。
            return self._clamp(name, args, f"参数不合法: {type(e).__name__}: {e}")

    def _clamp(self, name: str, args: dict, reason: str) -> dict:
        entry = {"tool": name, "ok": False, "clamped": reason, "args": args}
        self.clamp_log.append(entry)
        return entry

    # —— show / fx / timer / ask:M1 CLI 仅落 episode,UI 指令由客户端消费 ——
    def _t_show(self, name: str, a: dict) -> dict:
        vis = a.get("visibility", "全场公开")
        player = a.get("player")
        if vis in ("自己看", "额头"):
            if player not in self.state.players:
                raise ClampError(f"show({vis}) 必须指定在座玩家,收到: {player}")
        out = {"display": a.get("content", ""), "visibility": vis, "player": player}
        demo = a.get("demo")
        if demo:  # 只透传资产册里登记过的 ref——模型编的引用一律降级文字,局不断
            if demo in self.known_demo_refs:
                out["demo_ref"] = demo
            else:
                out["note"] = f"demo 引用不在资产册({demo}),已降级为纯文字"
        return out

    def _t_fx(self, name: str, a: dict) -> dict:
        return {"fx": a.get("effect", "")}

    def _t_timer(self, name: str, a: dict) -> dict:
        import time as _time
        secs = int(a.get("seconds", 10))
        if not 1 <= secs <= 600:
            raise ClampError(f"timer 秒数越界: {secs}")
        self.state.timers.append(_time.time() + secs)
        return {"timer_started": secs, "label": a.get("label", "")}

    def _t_ask(self, name: str, a: dict) -> dict:
        """限时问一嘴:开一个窗口,到点按多数认,一票也认,没人答就往下走。

        房主裁定:不要求全场共识——"和问一嘴没区别"。共识门槛会把局卡死,
        而现场问"这轮谁输了"本来就是谁应声算谁。
        """
        window = int(a.get("window", 5))
        if not 1 <= window <= 120:
            raise ClampError(f"ask 窗口越界: {window} 秒")
        # deadline 留空:计时从第一个人应声才开始。没人应声就一直等着,
        # 不催、不叫醒主持——房主裁定「等着回复就行,别疯狂 push 人」。
        self.state.open_ask = {
            "prompt": a.get("prompt", ""), "asked": a.get("player", "全场"),
            "options": a.get("options"), "deadline": None, "answers": {},
            "window": window,
        }
        return {"asked": a.get("player", "全场"), "prompt": a.get("prompt", ""),
                "options": a.get("options"), "window": window}

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
            return self._maybe_private({"picked": self.rng.choice(items)}, a)
        if name == "random.int":
            lo, hi = int(a.get("min", 1)), int(a.get("max", 6))
            return self._maybe_private({"value": self.rng.randint(lo, hi)}, a)
        raise ClampError(f"random 未知子操作: {name}")

    def _maybe_private(self, res: dict, a: dict) -> dict:
        """隐藏信息类玩法要能私密摇。

        实测两桌独立撞到同一个洞:摇毒杯号码的 value 明文进公共日志(比私发给
        目标还早一拍);抽卧底的 picked 当众公示,主持只能临场编掩护、改成自己
        拍脑袋定人——「隐藏角色的公平性完全依赖主持自律,系统一点都没保证」。
        标 visibility=自己看 时由 route_private 投进收件箱并遮蔽公开面。
        """
        vis = a.get("visibility")
        if vis in ("自己看", "额头"):
            player = a.get("player")
            if player not in self.state.players:
                raise ClampError(f"random({vis}) 必须指定在座玩家,收到: {player}")
            res["visibility"], res["player"] = vis, player
        return res

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
        if op == "settle":
            # 清账制缺的另一半:欠的口数喝掉了,账要能清零。
            # 没有这个动作时,主持只能把"喝一口"记成 -1,分数越滚越没有意义。
            player = a.get("player")
            targets = list(self.state.scores) if player in (None, "全场") else [player]
            if player not in (None, "全场") and player not in self.state.scores:
                raise ClampError(f"未知玩家: {player}")
            cleared = {}
            for p in targets:
                owed = -self.state.scores[p]
                if owed > 0:
                    cleared[p] = owed
                    self.state.settled[p] = self.state.settled.get(p, 0) + owed
                    self.state.scores[p] = 0
            return {"settled": cleared, "settled_total": dict(self.state.settled),
                    "scores": dict(self.state.scores)}
        if op == "discard":
            # 主动弃牌(隐私牌/重复牌)。抽过就已进 atoms_used,不留痕的话
            # 流水里弃牌和用牌长得一模一样,复盘分不清主持是用了还是撕了。
            aid = a.get("atom_id")
            if not aid:
                raise ClampError("state.discard 需要 atom_id")
            self.state.discards.append({"atom_id": aid, "reason": a.get("reason", "")})
            return {"discarded": aid, "reason": a.get("reason", "")}
        if op == "note":
            self.state.notes[a.get("key", "_")] = a.get("value")
            return {"noted": a.get("key", "_")}
        if op == "finish":
            self.state.finished = True
            return {"finished": True}
        raise ClampError(f"state 未知子操作: {op}")

    # —— draw_atom:接口先定库后换(M1=种子数组;M3 换 atoms.sqlite,签名不变) ——
    def _t_draw_atom(self, name: str, a: dict) -> dict:
        # 空池有四种成因,过去合成一句「无可用原子」,主持无法自我修复。
        # 逐条计数,报错时告诉它是哪一关卡住的。
        want_type = a.get("atom_type")
        if want_type and want_type not in ATOM_TYPES:
            raise ClampError(f"atom_type 不合法: {want_type!r};可用值: {'/'.join(sorted(ATOM_TYPES))}")
        why = {"已用过": 0, "野度超档": 0, "野度不够": 0, "分档不符": 0,
               "类型不符": 0, "道具不在场": 0}
        pool = []
        for atom in self.atom_pool:
            if atom["id"] in self.state.atoms_used or atom["id"] in a.get("exclude", []):
                why["已用过"] += 1
                continue
            if atom["wildness"] > min(self.state.wildness_cap, int(a.get("野度", 10))):
                why["野度超档"] += 1
                continue
            if atom["wildness"] < int(a.get("野度min", 0)):
                why["野度不够"] += 1
                continue  # 加档下限:说到做到,嘴上加档必须参数加档
            if a.get("tier") and atom.get("tier") != a["tier"]:
                why["分档不符"] += 1
                continue  # 价值分档过滤:铺垫拍/主打拍各取所需
            if want_type and atom["type"] != want_type:
                why["类型不符"] += 1
                continue
            if atom["props"] and not (set(atom["props"]) & set(self.state.scene_objects)):
                why["道具不在场"] += 1
                continue  # 实体门槛:现场没有所需实物则不抽(通用桌具按在场清单动态放行)
            pool.append(atom)
        if not pool:
            top = ", ".join(f"{k}{v}条" for k, v in
                            sorted(why.items(), key=lambda kv: -kv[1]) if v)
            raise ClampError(f"draw_atom 无可用原子——被挡在:{top}")
        atom = self.rng.choice(pool)
        self.state.atoms_used.append(atom["id"])
        if "skill" in atom:  # 技能授予型:自动登记权力(绑实物取现场匹配第一件)
            bound = next((o for o in atom["props"] if o in self.state.scene_objects), "")
            holder = a.get("grant_to") or self.state.focus or self.state.players[0]
            self.state.grants.append(SkillGrant(
                prop=atom["skill"]["prop"], holder=holder, bound_object=bound,
                uses_left=atom["skill"]["uses"], ritual=atom["skill"]["ritual"]))
        out = {"atom": {k: atom.get(k) for k in ("id", "name", "type", "text", "wildness", "currency", "tier")},
               "bound_object": next((o for o in atom["props"] if o in self.state.scene_objects), None)}
        card = self.pattern_by_atom.get(atom["id"])
        if card and card.get("demo_ref"):  # 演示件随骨架来:show 时带上 demo 字段即可播放
            out["demo"] = {"ref": card["demo_ref"], "pattern": card["name"]}
        return out
