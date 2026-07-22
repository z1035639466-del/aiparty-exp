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
SKILLS_FILE = "inputs/skills/skills-v1.jsonl"  # 技能牌单独库(2026-07-21 裁定):扩编批产+守门入库


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
        seeds = _P(__file__).parent / "atoms_seed.py"
        if "seeds_sha1" in meta and meta["seeds_sha1"] != hashlib.sha1(seeds.read_bytes()).hexdigest():
            db.close()
            return None  # 种子也是源:种子改了没重建,同样跌回 jsonl
        sk = _P(SKILLS_FILE)
        sk_sha = hashlib.sha1(sk.read_bytes()).hexdigest() if sk.exists() else "absent"
        if "skills_sha1" in meta and meta["skills_sha1"] != sk_sha:
            db.close()
            return None  # 技能库同为源
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
    skills = _P(SKILLS_FILE)
    if skills.exists():  # 技能单独库并入(执行层再按类型分池)
        for line in skills.read_text(encoding="utf-8").splitlines():
            try:
                s = json.loads(line)
            except json.JSONDecodeError:
                continue
            if s.get("id") and s["id"] not in seen and s.get("skill"):
                seen.add(s["id"])
                pool.append(s)
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
        # 技能池与内容池分离(房主裁定 2026-07-21):8 张权力卡混在近三千条
        # 惩罚/挑战里"根本抽不到,而且不是一个体量"。技能走 skill.deal 专用信道。
        _all = load_atom_pool(atoms_path)
        self.skill_pool = [a for a in _all if a.get("type") == "技能授予"]
        self.atom_pool = [a for a in _all if a.get("type") != "技能授予"]
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
            prev = self.state.pending_photo or self.state.pending_audio
            self.state.pending_photo = None
            self.state.pending_audio = None
            return {"cancelled": bool(prev)}
        if op not in ("photo", "audio"):
            raise ClampError(f"judge 未知子操作: {op}")
        player = a.get("player")
        if player not in self.state.players:
            raise ClampError(f"judge.{op} 必须指定在座玩家,收到: {player}")
        if self.state.pending_photo or self.state.pending_audio:
            raise ClampError("已有判定进行中(可 judge.cancel)")
        prompt = (a.get("prompt") or "").strip()
        if not prompt:
            raise ClampError(f"judge.{op} 需要 prompt(判什么,给裁判看的标准)")
        if op == "audio":
            self.state.pending_audio = {"player": player, "prompt": prompt}
            return {"requested": player, "note": "等他录音;结果以 judge_result 事件送达,期间别催"}
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
        if name == "random.dice":
            # 一把骰:count 颗六面骰(大话骰默认 5 颗)。prompt 里早就让主持这么调,
            # 工具却一直没实装——调了就被钳,骰子局全被逼成嘴报数。钳制:1–10 颗。
            count = int(a.get("count", 5))
            if not (1 <= count <= 10):
                raise ClampError(f"random.dice count 须在 1–10,收到: {count}")
            batch = a.get("players")
            if batch:
                # 批量暗骰:一次调用全桌各自摇,每人一把独立结果,互不可见——大话骰
                # 开局的正确姿势(真机实测:主持受每拍工具上限挤压,被逼一颗一颗发)。
                bad = [p for p in batch if p not in self.state.players]
                if bad:
                    raise ClampError(f"random.dice players 含不在座者: {bad}")
                rolls = {p: [self.rng.randint(1, 6) for _ in range(count)] for p in batch}
                return {"rolls": rolls, "players": list(batch), "visibility": "自己看"}
            dice = [self.rng.randint(1, 6) for _ in range(count)]
            # value 用 list——route_private 打 🎲 防伪水印,App 端只认水印画骰面
            return self._maybe_private({"value": dice}, a)
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

    # —— prop:实体道具发放(发盅,玩家自己摇)——
    # 房主原则:局长不能替玩家玩了再把结果告知——玩的动作在玩家手里,局长只发玩法。
    # random.dice 是局长替玩家摇(暗骰快递结果),违反这条;prop.dice_cup 只发一只
    # 「未摇的盅」到玩家手机,点数此刻不存在,玩家在 App 上自己摇(POST /api/event roll)。
    # 发放本身公开可见(桌上都知道谁有盅),点数不在这层产生——见 simulator.roll_cup。
    def _t_prop(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else "dice_cup"
        if op == "cancel":
            # 收盅:指定 players 收这几只,不指定收全部(重摇须局长重发,这里先清)
            batch = a.get("players") or ([a["player"]] if a.get("player") else None)
            targets = list(batch) if batch else list(self.state.props)
            cleared = [p for p in targets if self.state.props.pop(p, None) is not None]
            return {"cancelled": cleared}
        if op != "dice_cup":
            raise ClampError(f"prop 未知子操作: {op}")
        # 批量发盅:players 列表(大话骰=全员);单发也收 player。至少一名在座玩家。
        batch = a.get("players") or ([a["player"]] if a.get("player") else [])
        if not batch:
            raise ClampError("prop.dice_cup 需要 players(收盅的在座玩家,可批量)")
        bad = [p for p in batch if p not in self.state.players]
        if bad:
            raise ClampError(f"prop.dice_cup players 含不在座者: {bad}")
        # count:几颗骰,局长按玩法定(大话骰 5、快版 3),钳 1–10
        count = int(a.get("count", 5))
        if not (1 <= count <= 10):
            raise ClampError(f"prop.dice_cup count 须在 1–10,收到: {count}")
        # 每人挂一只「未摇的盅」(rolled=None);重复发=换新盅重置(覆盖旧盅)
        for p in dict.fromkeys(batch):  # 去重,同名只发一只
            self.state.props[p] = {"kind": "骰盅", "count": count, "rolled": None}
        return {"dealt": list(dict.fromkeys(batch)), "count": count, "kind": "骰盅",
                "note": "盅已发到各人手机,等他们自己摇(events 里看谁摇了);"
                        "点数玩家摇出来才有,别替他们摇——替玩=虚构同罪"}

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
        if want_type == "技能授予":
            return self._t_skill("skill.deal", a)  # 技能单独开库:老调法委托专用信道
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
        out = {"atom": {k: atom.get(k) for k in ("id", "name", "type", "text", "wildness", "currency", "tier")},
               "bound_object": next((o for o in atom["props"] if o in self.state.scene_objects), None)}
        card = self.pattern_by_atom.get(atom["id"])
        if card and card.get("demo_ref"):  # 演示件随骨架来:show 时带上 demo 字段即可播放
            out["demo"] = {"ref": card["demo_ref"], "pattern": card["name"]}
        return out

    # —— skill:技能牌专用信道(单独开库,2026-07-21 房主裁定)——
    # 权力卡与内容不是一个体量,混池等于抽不到。道具原语双态在此兑现:
    # 现场有匹配实物就绑定(换皮不改机制),没有就虚拟态照发(系统仪式补皮)
    # ——"道具不在场"永远不拦技能,那道闸只管实体门槛玩法。
    def _t_skill(self, name: str, a: dict) -> dict:
        op = name.split(".", 1)[1] if "." in name else "deal"
        if op == "transfer":
            return self._skill_transfer(a)  # 技能转手:抢夺/交换/截胡的唯一账面原语
        if op == "cancel":
            # 收回一张已发技能(发错人/发重了/持有人退场)。uses_left 清零=销账但留痕
            #(隔离不删除:grants 流水还在,复盘看得见这张牌发过又收了)。
            holder = a.get("holder")
            if holder not in self.state.players:
                raise ClampError(f"skill.cancel 持有人必须在座,收到: {holder}")
            prop = a.get("prop")
            g = next((g for g in self.state.grants
                      if g.holder == holder and g.uses_left > 0
                      and (not prop or g.prop == prop)), None)
            if g is None:
                raise ClampError(f"{holder} 名下没有可收回的技能" + (f"「{prop}」" if prop else ""))
            g.uses_left = 0
            return {"cancelled": g.prop, "holder": holder}
        if op != "deal":
            raise ClampError(f"skill 未知子操作: {op}")
        holder = a.get("grant_to") or self.state.focus or self.state.players[0]
        if holder not in self.state.players:
            raise ClampError(f"skill.deal 授予对象必须在座,收到: {holder}")
        cands = [s for s in self.skill_pool
                 if s["id"] not in self.state.atoms_used
                 and s["id"] not in a.get("exclude", [])
                 and s["wildness"] <= self.state.wildness_cap]
        held = {g.prop for g in self.state.grants if g.uses_left > 0}
        cands = [s for s in cands if s["skill"]["prop"] not in held]  # 同名技能不重发
        if not cands:
            raise ClampError("技能库发完了(或全被野度档拦住)——本局别再发,已发的用起来")
        atom = self.rng.choice(cands)
        self.state.atoms_used.append(atom["id"])
        bound = next((o for o in atom["props"] if o in self.state.scene_objects), "")
        self.state.grants.append(SkillGrant(
            prop=atom["skill"]["prop"], holder=holder, bound_object=bound,
            uses_left=atom["skill"]["uses"], ritual=atom["skill"]["ritual"]))
        return {"atom": {k: atom.get(k) for k in ("id", "name", "type", "text", "wildness", "currency")},
                "granted_to": holder, "uses": atom["skill"]["uses"],
                "ritual": atom["skill"]["ritual"],
                "bound_object": bound or None,
                "form": "实物绑定" if bound else "虚拟态(系统仪式补皮,照常发动)"}

    # —— skill.transfer:技能转手(唯一账面原语)——顺走王牌/手牌互换/优先购买权
    # 三张"抢夺·交换"卡此前被砍,砍点只有一个:引擎没有"把一张 grant 从 A 名下挪到
    # B 名下"的动作,主持嘴上转了、账本(digest.grants)没转 = 嘴账不一。这里补上。
    # 钳制:源无此牌→驳回(只回执,不公开出丑);目标已持同名→驳回(沿用同名不重发)。
    # 转手不是局长随意没收:只有技能牌自己文本写明可抢/可换/可截胡时才走这条。
    def _skill_transfer(self, a: dict) -> dict:
        src = a.get("from") or a.get("holder")
        dst = a.get("to") or a.get("grant_to")
        if src not in self.state.players:
            raise ClampError(f"skill.transfer 转出方必须在座,收到: {src}")
        if dst not in self.state.players:
            raise ClampError(f"skill.transfer 转入方必须在座,收到: {dst}")
        if src == dst:
            raise ClampError("skill.transfer 转出转入不能是同一人")
        # 源玩家名下可用技能(uses_left>0);指定 prop 就锁那张,不指定取名下第一张
        prop = a.get("prop")
        owned = [g for g in self.state.grants if g.holder == src and g.uses_left > 0]
        if prop:
            owned = [g for g in owned if g.prop == prop]
        if not owned:
            which = f"「{prop}」" if prop else "任何可用技能"
            # 源没这张牌就是没有:回执驳回,不当众宣布免得出丑(与私发同姿势)
            raise ClampError(f"{src} 名下没有{which},无法转手(嘴上转≠账上转,不许硬圆)")
        g = owned[0]
        # 目标已持同名技能:沿用现有那张,不叠一张同名(与 skill.deal 的同名不重发一致)
        if any(x.holder == dst and x.prop == g.prop and x.uses_left > 0
               for x in self.state.grants):
            raise ClampError(f"{dst} 已持有「{g.prop}」,同名不重发,转手驳回")
        g.holder = dst  # ledger 归属变更:digest.grants 里这张牌的 holder 随之翻到 dst
        # 私件挂账:转出/转入方各记一笔去向(只记 holder+kind、不记内容,与 show 私件同姿势)。
        # 内容(下面的 notices)按观看者遮蔽——每人只在自己收件箱看到自己那一份。
        self.state.private_out.append({"holder": src, "kind": "技能转出"})
        self.state.private_out.append({"holder": dst, "kind": "技能转入"})
        return {"transferred": g.prop, "from": src, "to": dst, "uses_left": g.uses_left,
                "notices": [
                    {"player": src, "text": f"你的技能「{g.prop}」已被转走,现归 {dst}"},
                    {"player": dst, "text": f"你得到技能「{g.prop}」(来自 {src},剩 {g.uses_left} 次)"
                                            f";发动仪式:{g.ritual}"},
                ]}
