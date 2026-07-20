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
ATOMS_DB = "inputs/atoms/atoms.sqlite"  # M3 换库:派生物,jsonl 是源;指纹不新鲜即跌回 jsonl


def _load_pool_from_db(db_path) -> list[dict] | None:
    """从 sqlite 取弹药池;库陈旧(源 jsonl 变了没重建)返回 None 跌回 jsonl——
    悄悄用陈旧库比没有库更糟。tools/build_atoms_db.py 重建。"""
    import hashlib
    import sqlite3
    from pathlib import Path as _P
    try:
        db = sqlite3.connect(str(db_path))
        meta = dict(db.execute("SELECT key, value FROM meta"))
        src = _P(ATOMS_FILE)
        src_sha = hashlib.sha1(src.read_bytes()).hexdigest() if src.exists() else "absent"
        if meta.get("atoms_jsonl_sha1") != src_sha:
            db.close()
            return None
        import json
        pool = []
        for (aid, name, typ, text, wild, tier, minp, currency,
             props, safety, opener, skill, _pat) in db.execute(
                "SELECT id,name,type,text,wildness,tier,min_players,currency,"
                "props,safety,opener,skill,pattern_id FROM atoms"):
            a = {"id": aid, "name": name, "type": typ, "text": text,
                 "wildness": wild, "props": json.loads(props), "safety": json.loads(safety),
                 "currency": currency, "tier": tier, "min_players": minp}
            if opener:
                a["opener"] = True
            if skill:
                a["skill"] = json.loads(skill)
            pool.append(a)
        db.close()
        return pool or None
    except Exception:
        return None  # 库坏了就当没有,jsonl 永远兜底


def load_atom_pool(atoms_path: str | None = None) -> list[dict]:
    """种子佐料 + 抽取库(confidence=high)合并;库文件缺席时纯种子。
    默认路径下若 atoms.sqlite 存在且指纹新鲜,直接走库(M3 换库,接口不变);
    显式传 atoms_path(测试/定制)一律走 jsonl 老路。"""
    import json
    from pathlib import Path as _P
    if atoms_path is None and _P(ATOMS_DB).exists():
        pool = _load_pool_from_db(ATOMS_DB)
        if pool is not None:
            return pool
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
    for atom in pool:
        if "tier" not in atom:
            atom["tier"] = _tier_of(atom)
        if "min_players" not in atom:
            atom["min_players"] = _min_players_of(atom)
    return pool


# —— 价值分档:铺垫(小快垫场)|主打(副歌)。判据按类型各自定义——
# 三桌实测战损:旧判据(敢不敢|短条件点名)让 完整玩法 整类拿不到铺垫标,
# 而 prompt 同时教「通用局抽完整玩法」和「垫场用铺垫」,组合必空返,
# 2331 条弹药三桌只抽出 2 条。分档和类型是正交维度,不许实现成强相关。
_TIER_LEN = {"条件点名": 18, "完整玩法": 20, "任务内容": 13, "道具挑战": 14, "问答题目": 14}


def _tier_of(atom: dict) -> str:
    t, typ = atom.get("text", ""), atom.get("type", "")
    if typ == "技能授予":
        return "主打"   # 授技能是重器,没有垫场形态
    if typ == "规则修饰":
        return "铺垫"   # 规则佐料天然是垫场
    if re.search(r"不敢的|敢不敢", t) or atom.get("opener"):
        return "铺垫"   # 敢不敢微挑战 / 种子开局款:快拍
    return "铺垫" if len(t) <= _TIER_LEN.get(typ, 15) else "主打"


# —— 机制三型 → 人数下限(DM-skill v2.1.1【适配参考】判据落码;三桌实测三向验证)——
# 候选池型:谜底跨轮持久且从在场玩家中锁定 → N≤5 禁作核心循环(信息坍缩)
# 分队/传递链:结构性下限;对抗型(叫价/竞速/对决)N≥3;
# 广播型是默认:群体动作要 3 人起,二十问/猜码/额头/个人挑战 2 人亦成立。
_POOL_TYPE = re.compile(r"卧底|内鬼|狼人|杀手|隐藏身份|谁是|间谍|平民票|真凶")
_TEAM = re.compile(r"分队|组队|两队|车轮战|团战")
_CHAIN = re.compile(r"传话|依次传|接力|轮流传|传给下一")
_VERSUS = re.compile(r"对决|擂台|1v1|诈唬|叫价|比大小|划拳|对拳|两人一组|竞速|抢答")
_CROWD = re.compile(r"所有人|全场|大家|围圈|每个人|全员|集体|其余人|在场")


def _grams3(t: str) -> frozenset:
    t = re.sub(r"[\s,。;;:、!?!?~·..「」()()0-9]+", "", t)
    if len(t) < 3:
        return frozenset([t]) if t else frozenset()
    return frozenset(t[i:i + 3] for i in range(len(t) - 2))


def _jac(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter) if inter else 0.0


def _min_players_of(atom: dict) -> int:
    t = atom.get("text", "") + atom.get("name", "")
    if _POOL_TYPE.search(t):
        return 6            # 候选池型:N≤5 信息坍缩(4 人卧底两三句被交叉验证穿)
    if _TEAM.search(t):
        return 4
    if _CHAIN.search(t) or _VERSUS.search(t):
        return 3
    if _CROWD.search(t):
        return 3            # 群体广播:2 人桌没有"全场"
    return 2                # 二十问/猜码/额头/个人挑战类:2 人亦成立


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
        player, players = a.get("player"), a.get("players")
        if vis == "自己看" and players:
            # 批量私发(8 人桌发牌 4 个回合的死气,根因就是一次只能发一张):
            # 平民词一批发 N 人、卧底词单发,两次调用收工。
            bad = [p for p in players if p not in self.state.players]
            if bad:
                raise ClampError(f"show(自己看) players 含非在座玩家: {bad}")
        elif vis in ("自己看", "额头"):
            if player not in self.state.players:
                raise ClampError(f"show({vis}) 必须指定在座玩家,收到: {player}")
        out = {"display": a.get("content", ""), "visibility": vis, "player": player}
        if vis == "自己看" and players:
            out["players"] = list(players)
        if vis in ("自己看", "额头"):  # 私件挂账:防发完就沉底(不记内容,只记去向)
            for holder in (players or [player]):
                self.state.private_out.append({"holder": holder, "kind": vis})
        demo = a.get("demo")
        if demo:  # 只透传资产册里登记过的 ref——模型编的引用一律降级文字,局不断
            if demo in self.known_demo_refs:
                out["demo_ref"] = demo
            else:
                out["note"] = f"demo 引用不在资产册({demo}),已降级为纯文字"
        return out

    def _t_fx(self, name: str, a: dict) -> dict:
        return {"fx": a.get("effect", "")}

    # —— duel:快枪手对决(手机原生旗舰件,设计单:手机原生通用游戏 v0)——
    # 系统在随机时点亮「拔!」,先拍屏者胜、抢跑判负,毫秒判定公平由系统保证。
    # 拔枪时点连主持都保密(返回值不含 draw_at,荷官回执因此也拿不到)。
    def _t_duel(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else "start"
        if op == "cancel":
            prev, self.state.duel = self.state.duel, None
            return {"cancelled": bool(prev)}
        if op != "start":
            raise ClampError(f"duel 未知子操作: {op}")
        ps = list(a.get("players") or [])
        if len(ps) != 2 or len(set(ps)) != 2:
            raise ClampError(f"duel 需要两名不同玩家,收到: {ps}")
        bad = [p for p in ps if p not in self.state.players]
        if bad:
            raise ClampError(f"duel 含非在座玩家: {bad}")
        if self.state.duel:
            raise ClampError("已有对决进行中(可先 duel.cancel)")
        import time as _t
        self.state.duel = {"players": ps, "taps": {},
                           "draw_at": _t.time() + self.rng.uniform(2.0, 8.0)}
        return {"duel": ps, "note": "对峙开始;拔枪时点系统保密,结果以 duel_result 事件送达,期间别催"}

    # —— judge:拍照判定(多模态判定通道 v0)——主持显式发起的判定时刻,
    # 非常驻监听(感知线收束裁定)。流程:主持点人出题 → 目标手机拍照上传 →
    # 视觉模型判定 → judge_result 事件回主持。判不了走共识兜底(§1.4)。 ——
    def _t_judge(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else "photo"
        if op == "cancel":
            prev, self.state.pending_photo = self.state.pending_photo, None
            return {"cancelled": bool(prev)}
        if op != "photo":
            raise ClampError(f"judge 未知子操作: {op}")
        player = a.get("player")
        if player not in self.state.players:
            raise ClampError(f"judge.photo 必须指定在座玩家,收到: {player}")
        if self.state.pending_photo:
            raise ClampError("已有拍照判定进行中(可 judge.cancel)")
        prompt = (a.get("prompt") or "").strip()
        if not prompt:
            raise ClampError("judge.photo 需要 prompt(判什么,给视觉裁判看的标准)")
        self.state.pending_photo = {"player": player, "prompt": prompt}
        return {"requested": player, "note": "等他拍照;结果以 judge_result 事件送达,期间别催"}

    # —— music:AI 局头当 DJ。歌单是房主上传的资产(真人可写、AI 只读只调),
    # 模型只发「放这首」的意图,播放由运行时执行;点歌单外的歌 = 钳制,
    # 与 demo_ref 资产册同一姿势——防的是主持幻觉出一首不存在的歌。 ——
    def _t_music(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else a.get("op", "play")
        if op == "stop":
            prev, self.state.now_playing = self.state.now_playing, None
            return {"stopped": prev}
        if op != "play":
            raise ClampError(f"music 未知子操作: {op}")
        if not self.state.playlist:
            raise ClampError("本局没有歌单(房主未上传),music 不可用")
        want = (a.get("track") or "").strip()
        if not want:
            raise ClampError("music.play 需要 track")
        hits = [t for t in self.state.playlist if t == want] \
            or [t for t in self.state.playlist if want.lower() in t.lower()]
        if not hits:
            sample = "、".join(self.state.playlist[:5])
            raise ClampError(f"歌单里没有「{want}」——只许点已上传的歌(如: {sample}…)")
        if len(hits) > 1:
            raise ClampError(f"「{want}」在歌单里有 {len(hits)} 首撞名: {'、'.join(hits[:4])};说全名")
        self.state.now_playing = hits[0]
        return {"playing": hits[0], "mood": a.get("mood", "")}

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
        if a.get("mode") == "轮流":
            # 顺序性玩法(一人一句形容/逐个表态)的正解:每人独立应答槽逐个开窗,
            # 不再全场抢答——实测抢答窗把慢玩家和后手挤成"安静得可疑"。
            order = [p for p in (a.get("order") or self.state.players)
                     if p in self.state.players]
            if not order:
                raise ClampError("ask(轮流) 需要至少一名在座玩家")
            import time as _t
            deadline = _t.time() + window
            self.state.open_ask = {
                "prompt": a.get("prompt", ""), "asked": order[0],
                "options": a.get("options"), "deadline": deadline, "answers": {},
                "window": window, "mode": "轮流", "queue": order[1:],
                "order_all": order,
            }
            self.state.timers.append(deadline)
            return {"asked": order[0], "prompt": a.get("prompt", ""), "mode": "轮流",
                    "order": order, "window": window,
                    "note": "逐人开窗,答完或超时自动轮下一位,收齐出 ask_result"}
        # 抢答(默认):deadline 留空,计时从第一个人应声才开始。没人应声就一直
        # 等着,不催、不叫醒主持——房主裁定「等着回复就行,别疯狂 push 人」。
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
               "类型不符": 0, "道具不在场": 0, "人数不够": 0}
        n_players = len(self.state.players)
        pool = []
        for atom in self.atom_pool:
            if atom["id"] in self.state.atoms_used or atom["id"] in a.get("exclude", []):
                why["已用过"] += 1
                continue
            if atom.get("min_players", 2) > n_players:
                # 机制下限自动过滤,不靠模型自觉:2 人桌抽不到抓手指,
                # 5 人以下抽不到卧底核心循环(候选池型信息坍缩)。
                why["人数不够"] += 1
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
        ctx = str(a.get("context") or "").strip()
        if ctx and len(pool) > 3:
            # 语境软收窄(六桌实测:'库不懂主持在铺什么局',卧底局抽出跳舞传递):
            # 有正相关命中就只在命中里随机(前 12,保留随机性——检索不夺权只递刀);
            # 零命中当没说,整池照抽,不因为语境把局憋死。
            cg = _grams3(ctx)
            scored = sorted(((at, _jac(cg, _grams3(at.get("name", "") + at.get("text", ""))))
                             for at in pool), key=lambda x: -x[1])
            hits = [at for at, sc in scored if sc > 0]
            if hits:
                pool = hits[:12]
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
