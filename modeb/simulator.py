"""M2 · 多机模拟台(开发驾驶舱,非产品面)。

一页 = N 个玩家 pane(2–10 任意,机位数不改机制)+ 主持台 + 账本/episode 实时窗。
玩家事件 → engine.push_event;回合驱动:manual(主持台提交决策)或 scripted。
纯标准库(http.server),零依赖;产品端另起炉灶,本台不为浏览器妥协任何设计
(裁定纪要:浏览器损失逐件上报——本台非产品,无此问题)。

用法:python -m modeb.simulator [--port 8747]  → 浏览器开 http://localhost:8747
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import secrets
import threading
import time as _t
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import persist, tts
from .driver_llm import LLMDriver
from .driver_scripted import ScriptedDriver
from .engine import Engine
from .player_agent import LLMPlayerAgent, ScriptedPlayerAgent
from .state import GameState
from .transports import CallMeter, MeteredTransport, Resilient, make_transport

MIN_PLAYERS, MAX_PLAYERS = 2, 10  # 机位上限只是这一行常数,机制不感知机位数

# 房间码字母表:去掉易混的 0O1I,4 位约 70 万种,一台服务器同时跑多桌够用
ROOM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
ROOM_CODE_LEN = 4
IDLE_LIMIT_S = 2 * 3600          # 2 小时无活动即回收房间(episode 文件保留)
DEFAULT_MAX_LLM_CALLS = 500      # 每局 LLM 调用默认上限(0=不限);环境变量可覆盖


def gen_room_code(taken: set[str]) -> str:
    """生成一个不与现有房间冲突的易读房间码(如 A7QK)。"""
    while True:
        code = "".join(random.choice(ROOM_ALPHABET) for _ in range(ROOM_CODE_LEN))
        if code not in taken:
            return code


def _make_audio_judge():
    """音频裁判工厂。两条路,任一可用即通:
    ① 专口:AUDIO_JUDGE_BASE/KEY/MODEL(任意 OpenAI 兼容音频口);
    ② 千问一家:只有 DASHSCOPE_API_KEY 时自动用 qwen-omni——一把 key 全模态通。"""
    import os
    from .transports import CN_PROVIDERS, OpenAICompatTransport
    base, model = os.environ.get("AUDIO_JUDGE_BASE"), os.environ.get("AUDIO_JUDGE_MODEL")
    if base and model and os.environ.get("AUDIO_JUDGE_KEY"):
        return OpenAICompatTransport(model, base, "AUDIO_JUDGE_KEY")
    if os.environ.get("DASHSCOPE_API_KEY"):  # 千问一家:同一把 key 直接调全模态
        from .transports import base_for
        return OpenAICompatTransport(
            os.environ.get("AUDIO_JUDGE_MODEL", "qwen3.5-omni-plus"),
            base_for("qwen"), "DASHSCOPE_API_KEY")
    return None


# 按住说话转写提示词(PTT 裁定 2026-07-24):只要逐字稿,不要裁判意见——
# 转写口与 judge_audio 共享全模态 transport,但职责完全不同(那边判"像不像",
# 这边只当耳朵)。改口径只改这一行。
STT_PROMPT = "逐字转写这段中文口语,只输出说的内容本身,不解释不评论,不加任何前后缀。"


class ManualDriver:
    """主持台驱动:/api/turn 随请求带入决策,decide 原样返回。"""

    def __init__(self) -> None:
        self.pending: dict | None = None

    def decide(self, digest: dict, events: list[dict]) -> dict:
        d = self.pending or {"text": "", "tool_use": []}
        self.pending = None
        return d


# 出厂默认走环境变量,.env 配一次就是全局开关(手机开局不带 provider 时也吃这个)。
# 不设则沿用原来的 anthropic/sonnet,老行为不变。
def _default_provider() -> str:
    return os.environ.get("YAPPA_PROVIDER") or "anthropic"


def _default_model() -> str:
    return os.environ.get("YAPPA_MODEL") or "sonnet"


class Session:
    def __init__(self, players: list[str], minutes: int, wildness: int,
                 objects: list[str], driver_kind: str, out_dir: Path,
                 bots: dict[str, str] | None = None, provider: str | None = None,
                 host_model: str | None = None, seat_model: str | None = None,
                 score_style: str = "自动", host_perception: str = "转写",
                 playlist: list[str] | None = None,
                 occasion: str = "", scene_brief: str = "",
                 state: GameState | None = None,
                 episode_path: Path | None = None, episode_mode: str = "w",
                 max_llm_calls: int | None = None) -> None:
        if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS:
            raise ValueError(f"玩家数须在 {MIN_PLAYERS}–{MAX_PLAYERS}(收到 {len(players)})")
        if driver_kind == "scripted" and len(players) < 3:
            raise ValueError("scripted 演示驱动需要 ≥3 人;manual 不限")
        bots = bots or {}
        unknown = set(bots) - set(players)
        if unknown:
            raise ValueError(f"bot 座位不在玩家名单里: {sorted(unknown)}")
        # state 传入 = 局中断点续局:直接接管盘上重建的账本,不新建空局
        #(驱动器/桌友仍按 cfg 重建——LLM 主持凭 digest 重新入场,history 不持久化)。
        if state is not None:
            self.state = state
        else:
            self.state = GameState(players=players, wildness_cap=wildness,
                                   time_budget_min=minutes, scene_objects=objects,
                                   score_style=score_style, host_perception=host_perception,
                                   playlist=[t.strip() for t in (playlist or []) if t.strip()],
                                   occasion=occasion.strip(), scene_brief=scene_brief.strip())
        self.driver_kind = driver_kind
        # 存局 cfg 用:恢复时按原 cfg 重建驱动/桌友,快照落盘也回读这些
        provider = provider or _default_provider()
        host_model = host_model or _default_model()
        seat_model = seat_model or _default_model()
        self.provider = provider
        self.host_model = host_model
        self.seat_model = seat_model
        self.bots_cfg = dict(bots)
        self.autoplay = False           # 由 Hub.start / restore 注入
        self.autoplay_interval_s = 1.0
        # —— 计费闸:一局一块计量表,主持+桌友的真实调用共用 —— #
        # cfg 未传时读环境变量 MAX_LLM_CALLS_PER_GAME,再退默认 500;0=不限。
        if max_llm_calls is None:
            max_llm_calls = int(os.environ.get("MAX_LLM_CALLS_PER_GAME", DEFAULT_MAX_LLM_CALLS))
        self.meter = CallMeter(max_llm_calls)
        if driver_kind == "scripted":
            self.driver = ScriptedDriver()
        elif driver_kind == "llm":
            # 主持包两层:内层 MeteredTransport 计费闸(到限抛 BudgetGateError→静默拍),
            # 外层 Resilient 瞬时重试(等 2 秒再试一次);桌友不包重试,已有响亮降级。
            self.driver = LLMDriver(
                Resilient(MeteredTransport(make_transport(provider, host_model), self.meter)),
                players, wildness, minutes, score_style=score_style,
                playlist=self.state.playlist,
                occasion=self.state.occasion,
                scene_brief=self.state.scene_brief)
        else:
            self.driver = ManualDriver()
        # 视觉裁判(judge.photo/场景扫描):随主持 provider 走对应视觉模型——
        # 千问一家即可通(qwen-vl),不再硬绑 anthropic。
        from .transports import vision_model_for
        self._judge_provider = provider
        self._judge_model = vision_model_for(provider, host_model)
        self._judge = None
        # 场景照更新 scene_brief 后重建主持 system 用
        self._prompt_args = dict(players=players, wildness=wildness, minutes=minutes,
                                 score_style=score_style)
        if provider == "mock":  # 测试:确定性假人(不烧调用,不过计费闸)
            self.bots = [ScriptedPlayerAgent(n, [[{"type": "laugh"}]] * 3) for n in bots]
        else:
            # 桌友调用同过计费闸:到限时 run_turn 直接跳过桌友那拍(不再进这块表)
            self.bots = [LLMPlayerAgent(n, persona or "普通桌友",
                                        MeteredTransport(make_transport(provider, seat_model), self.meter))
                         for n, persona in bots.items()]
        self.bot_names = sorted(bots)
        if episode_path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            episode_path = out_dir / f"sim_{stamp}.jsonl"
        self.episode_path = episode_path
        # 快照与 episode 同目录同 stamp:sim_<stamp>.jsonl ↔ sim_<stamp>.state.json
        self.state_path = persist.state_path_for(episode_path)
        self.engine = Engine(self.state, self.driver, self.episode_path, episode_mode=episode_mode)
        self.recent: list[dict] = []  # 最近回合行,供前端轮询
        # 设备匿名ID↔座位(用户数据层地基):episode 里同步留 device_bind 元信息行
        self.device_map: dict[str, str] = {}
        # 局长开口缓存:{(turn, voice): 音频字节}。Session 即房间,天然按 (room,line)
        # 隔离;同一句只烧一次 TTS 钱,进程内存活、不落盘(音频可重合成,不值得持久化)
        self.tts_cache: dict[tuple, bytes] = {}
        self.inbox: dict[str, list[str]] = {n: [] for n in players}  # 私发收件箱(可见性引擎落地)
        self.last_timing: dict = {}  # 上一拍耗时拆帐:host_ms / bots_ms(慢要先量再修)
        self.lock = threading.Lock()
        self.join_base = ""   # 由 Hub 注入:http://<局域网IP>:<端口>
        self.room_code = ""   # 由 Hub.start / restore 注入:本桌 4 位房间码
        self.last_active = _t.time()  # 最近一次被访问的时刻,闲置回收据此判活
        self.closed = False   # 闲置回收信号:autoplay 线程见到即退出(随房间生命周期)

    def route_private(self, line: dict) -> dict:
        """show(自己看/额头) 路由进收件箱;返回轮询面用的遮蔽版回合行。
        episode 文件保留全文(审计);/api/state 面上私密内容一律遮蔽——
        防偷看必须在服务端成立,不能靠前端自觉(与感知档同理)。"""
        red = copy.deepcopy(line)
        calls = red.get("tool_use", [])  # 与 results 同序同长(engine.turn 保证)
        for i, r in enumerate(red.get("results", [])):
            res = r.get("result") if isinstance(r, dict) else None
            if not (r.get("ok") and isinstance(res, dict)):
                continue
            # 技能转手:一次调用要私件知会两个人(转出方/转入方),内容各不相同——
            # 单 player 的遮蔽路子塞不下,走 notices 列表:每份投进各自收件箱,公开面只留摘要。
            notices = res.get("notices")
            if isinstance(notices, list) and notices:
                delivered = 0
                for n in notices:
                    pl, txt = n.get("player"), n.get("text")
                    if pl in self.inbox and txt:
                        self.inbox[pl].append(f"🔒 {txt}")
                        delivered += 1
                res["notices"] = f"🔒技能转手私件(已投递 {delivered} 人,内容仅各自可见)"
                continue
            vis, target = res.get("visibility"), res.get("player")
            # 批量暗骰(random.dice players=[…]):每人一把独立结果,各投各的。
            # "🔒🎲"(锁后紧跟骰、无空格)是引擎防伪水印:show 私发走 "🔒 {文案}"
            # 永远带空格,主持在文案里写 🎲 也伪造不出这个前缀——App 只认水印画骰面,
            # 模型编的假骰子从此上不了骰面(真机实测抓到过一次)。
            rolls = res.get("rolls")
            if vis == "自己看" and isinstance(rolls, dict):
                for pl, dice in rolls.items():
                    if pl in self.inbox:
                        self.inbox[pl].append(f"🔒🎲 {dice}")
                res["rolls"] = f"🔒批量暗骰(已投递 {len(rolls)} 人,点数仅各自可见)"
                continue
            # 牌卡私发(prop.card):发牌动作公开(桌上知道谁收到一张什么类型的牌),
            # 牌面内容只投本人收件箱(🎴 牌卡专用前缀,区别于 🔒 杂项私件/👀 额头),
            # 公开面/别人的 view 一律遮。荷官回执(_last_results)拿遮蔽前原文,局长可对账。
            if vis == "牌卡":
                holders = res.get("card_holders") or []
                kind = res.get("kind", "牌")
                secret = res.get("content", "")
                for pl in holders:
                    if pl in self.inbox:
                        self.inbox[pl].append(f"🎴 [{kind}] {secret}")
                masked = f"🎴发牌·{kind}(已投递 {len(holders)} 人,牌面仅本人可见)"
                res["content"] = masked
                if (i < len(calls) and isinstance(calls[i].get("input"), dict)
                        and "content" in calls[i]["input"]):
                    calls[i]["input"]["content"] = masked
                continue
            # 秘密载荷可能在 display(show)、也可能在 value/picked(random 私密摇)
            field = next((k for k in ("display", "value", "picked") if k in res), None)
            if field is None:
                continue
            secret = res[field]
            batch = res.get("players")
            if vis == "自己看" and batch:
                for pl in batch:
                    self.inbox[pl].append(f"🔒 {secret}")
                # 只遮内容不遮收件人:/api/state 是驾驶舱(非产品面),房主调试时
                # 本来就该看见谁拿到了什么;真实玩家走 /api/view,看不到这个端点。
                res[field] = f"🔒批量私发(已投递 {len(batch)} 人,内容仅目标可见)"
            elif vis == "自己看" and target:
                # 单人暗骰同样打防伪水印;show/pick/int 维持带空格的普通锁,水印仿不出
                if field == "value" and isinstance(secret, list):
                    self.inbox[target].append(f"🔒🎲 {secret}")
                else:
                    self.inbox[target].append(f"🔒 {secret}")
                res[field] = "🔒私发(已投递,内容仅目标可见)"
            elif vis == "额头" and target:
                # 额头牌=长在人身上的道具:状态化存 foreheads(App 点人看牌),
                # 私件流水里仍留 👀 行(向后兼容老客户端)
                self.state.foreheads[target] = str(secret)
                for pl in self.state.players:
                    if pl != target:
                        self.inbox[pl].append(f"👀 额头·{target}: {secret}")
                res[field] = f"👀额头牌(仅 {target} 本人不可见,已发其余人)"
            else:
                continue
            # 结果遮了、指令没遮等于没遮:tool_use[i].input 里是同一份原文与同一个收件人
            if i < len(calls) and isinstance(calls[i].get("input"), dict):
                if "content" in calls[i]["input"]:
                    calls[i]["input"]["content"] = res[field]
        return red

    def bind_device(self, player: str, device_id: str) -> None:
        """设备匿名ID↔座位绑定(账号体系的地基,须持 self.lock 调用)。
        首见即绑、只绑一次、写进 episode 审计线——将来账号系统上线,历史局按
        device_id 一键认领,今晚起的每一局都不浪费。座位已绑别的设备时不覆盖
        (中途换机的归属问题留给账号层裁,裸 ID 之间不许互抢)。"""
        if not device_id or player not in self.state.players:
            return
        dev = str(device_id)[:64]
        if player in self.device_map:
            return
        self.device_map[player] = dev
        self.engine.log_meta("device_bind", {"player": player, "device_id": dev,
                                             "room": self.room_code})

    def roll_cup(self, me: str) -> dict:
        """玩家自己摇盅(玩的动作留在玩家手里,房主原则:局长不替玩家玩)。须持 self.lock 调用。

        校验:有未摇的盅才许摇;一盅一摇,摇过再摇驳回(防赖账,重摇须局长重发)。
        引擎 RNG 出点数 → 写进本人 props.rolled;点数经现有 🔒🎲 防伪水印路投进本人
        inbox(App 只认水印画骰面);公开事件面只出「谁摇了骰盅」(无点数),经 push_event
        进事件流让主持与旁人看到动作;点数另走引擎对账信道(turn 里从 props 现取)给主持开牌用。
        """
        prop = self.state.props.get(me)
        if not prop or prop.get("kind") != "骰盅":
            return {"ok": False, "error": "你手上没有骰盅,等局长发"}
        if prop.get("challenged_by"):
            # 开牌后全桌盅锁定:点数就是清算的证据,开了牌还许摇等于让人当庭改口供
            return {"ok": False, "error": "已开牌,全桌骰盅锁定(等局长清算,收盅/重发后再摇)"}
        if prop.get("rolled") is not None:
            return {"ok": False, "error": "这盅已经摇过了(一盅一摇;想重摇得局长重发)"}
        count = int(prop.get("count", 5))
        # 引擎 RNG 出点数(与 random.dice 同一把 rng,公平由系统保证)
        dice = [self.engine.tools.rng.randint(1, 6) for _ in range(count)]
        prop["rolled"] = dice
        # 点数进本人私件:复用引擎防伪水印「🔒🎲」(锁后紧跟骰、无空格),App 端只认它画骰面
        if me in self.inbox:
            self.inbox[me].append(f"🔒🎲 {dice}")
        # 公开事件面只留动作、不留点数:主持从 events 看谁摇了、齐了开吹牛;旁人 view 也只见动作
        self.engine.push_event({"type": "roll", "player": me})
        return {"ok": True, "rolled": True, "count": count}

    def challenge(self, me: str, bid: dict | None = None) -> dict:
        """玩家拍「开牌!」——大话骰唯一进系统的判定时刻(与快枪手的「拔」同类)。须持 self.lock 调用。

        宪法:叫价博弈永远留在嘴上不进系统;唯独开牌是判定时刻,做成按钮。
        条件:本人持已摇的骰盅才可开(没盅/没摇驳回);一局一开——桌上已有未清算的
        开牌(局长还没 prop.cancel/重发)时再开驳回。可选 bid={count,face} 是被开的
        那口叫价(桌上本来就是喊出来的,公开不泄密;越界钳到 count 1–30 / face 1–6)。
        效果:公开 challenge 事件进事件流(局长与全桌可见,局长配对账信道点数即可清算);
        全桌骰盅立「已开牌」标(challenged_by/bid)并锁定不可再摇——点数即证据,
        解锁靠局长清算后收盅/重发。
        """
        prop = self.state.props.get(me)
        if not prop or prop.get("kind") != "骰盅":
            return {"ok": False, "error": "你手上没有骰盅,开不了牌"}
        if prop.get("rolled") is None:
            return {"ok": False, "error": "你的盅还没摇,摇了才有资格开牌"}
        # 骰子都下锅才算数(真机实测:有人没摇完就被开牌,全桌锁死只能等局长重发)
        unrolled = [p for p, pr in self.state.props.items()
                    if pr.get("kind") == "骰盅" and pr.get("rolled") is None]
        if unrolled:
            return {"ok": False,
                    "error": f"还有人没摇完({'、'.join(unrolled)}),骰子都下锅才能开牌"}
        if any(pr.get("challenged_by") for pr in self.state.props.values()):
            return {"ok": False, "error": "这一口已经有人开过牌了,等局长清算(收盅/重发后才能再开)"}
        clean = None
        if isinstance(bid, dict) and bid:
            try:  # 叫价钳制:count 1–30(全桌颗数上限量级)、face 1–6;解析不了当没带
                clean = {"count": max(1, min(30, int(bid.get("count", 1)))),
                         "face": max(1, min(6, int(bid.get("face", 1))))}
            except (TypeError, ValueError):
                clean = None
        for pr in self.state.props.values():
            pr["challenged_by"] = me
            pr["bid"] = clean
        ev = {"type": "challenge", "player": me}
        if clean:
            ev["bid"] = dict(clean)   # 「⚡ 甲开牌!(叫价:3个6)」——各面按此渲染
        self.engine.push_event(ev)
        return {"ok": True, "challenged": True, "bid": clean}

    def judge_photo(self, player: str, image_b64: str | None, media_type: str | None,
                     frames: list[str] | None = None) -> dict:
        """视觉裁判(锁外调用,慢):返回 judge_result 事件体。判不了不装懂——
        「无法判定」明说,主持按声明走 ask 共识兜底(spec §1.4)。
        frames:视频判定走的通道——客户端已把短视频抽成几张连续帧(仍是图片),
        这里原样多 image block 一起送审,裁判口径与单张照片判定不变。"""
        pend = self.state.pending_photo or {}
        if self._judge is None:
            self._judge = make_transport(self._judge_provider, self._judge_model)
        sys_p = ("你是聚会游戏的视觉裁判:只按给定标准判,宽松判、气氛优先、拿不准给过。"
                 '只输出 JSON:{"verdict":"过|不过|无法判定","reason":"一句话,现场能念"}')
        images = frames if frames else [image_b64]
        content = [{"type": "image", "source": {"type": "base64",
                                                 "media_type": media_type or "image/jpeg",
                                                 "data": b64}} for b64 in images]
        text = (f"这是同一段动作视频的连续抽帧(共{len(images)}帧,按时间顺序):判定标准:{pend.get('prompt', '')}"
                if frames else f"判定标准:{pend.get('prompt', '')}")
        content.append({"type": "text", "text": text})
        msgs = [{"role": "user", "content": content}]
        try:
            import re as _re
            raw = self._judge.complete(sys_p, msgs)
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            out = json.loads(m.group(0)) if m else {}
        except Exception as e:
            out = {"verdict": "无法判定", "reason": f"裁判失联({type(e).__name__})"}
        verdict = out.get("verdict")
        if verdict not in ("过", "不过", "无法判定"):
            verdict = "无法判定"
        return {"type": "judge_result", "player": player, "verdict": verdict,
                "reason": str(out.get("reason") or "")[:100]}

    def scene_photo(self, image_b64: str, media_type: str | None) -> dict:
        """开局一拍照(锁外调用,慢):现场照 → 实物清单 + 场景速写。
        一次性显式动作,非常驻监听(感知线收束);更新后重建主持 system。"""
        if self._judge is None:
            self._judge = make_transport(self._judge_provider, self._judge_model)
        sys_p = ('你是聚会现场侦察员。从照片提取可入游戏的信息,只输出 JSON:'
                 '{"objects": ["现场实物,可作道具的,如 瓶子/冰块/抱枕/投影"],'
                 '"brief": "一句场景速写(在哪/什么氛围/有什么可玩的)"}')
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": media_type or "image/jpeg",
                                         "data": image_b64}},
            {"type": "text", "text": "提取"}]}]
        try:
            import re as _re
            raw = self._judge.complete(sys_p, msgs)
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            out = json.loads(m.group(0)) if m else {}
        except Exception as e:
            return {"error": f"侦察失败({type(e).__name__}),照旧手填"}
        objs = [str(o) for o in (out.get("objects") or []) if str(o).strip()][:20]
        brief = str(out.get("brief") or "").strip()[:120]
        with self.lock:
            merged = list(dict.fromkeys([*self.state.scene_objects, *objs]))
            self.state.scene_objects = merged
            if brief:
                self.state.scene_brief = brief
            if isinstance(self.driver, LLMDriver):
                from .driver_llm import build_system_prompt
                self.driver.system = build_system_prompt(
                    self._prompt_args["players"], self._prompt_args["wildness"],
                    self._prompt_args["minutes"], self._prompt_args["score_style"],
                    self.state.playlist, self.state.occasion, self.state.scene_brief)
        return {"objects": objs, "brief": brief, "scene_objects": merged}

    def judge_audio(self, player: str, audio_b64: str, fmt: str | None) -> dict:
        """音频裁判(锁外调用,慢):语调打分/口令复述这类"像不像"判断。
        管路已通,裁判待接入——设 AUDIO_JUDGE_BASE/KEY/MODEL(OpenAI 兼容
        音频口,如 qwen-audio)即生效;未接入时诚实报「无法判定」,主持走
        共识兜底。分贝/音高这类纯信号活归 App 端 DSP,不走这里。"""
        pend = self.state.pending_audio or {}
        judge = _make_audio_judge()
        if judge is None:
            return {"type": "judge_result", "player": player, "verdict": "无法判定",
                    "reason": "音频裁判未接入(设 AUDIO_JUDGE_BASE/KEY/MODEL 即通)"}
        sys_p = ("你是聚会游戏的听觉裁判:只按给定标准判,宽松判、气氛优先、拿不准给过。"
                 '只输出 JSON:{"verdict":"过|不过|无法判定","reason":"一句话,现场能念"}')
        msgs = [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio_b64,
                                                    "format": fmt or "wav"}},
            {"type": "text", "text": f"判定标准:{pend.get('prompt', '')}"}]}]
        try:
            import re as _re
            raw = judge.complete(sys_p, msgs)
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            out = json.loads(m.group(0)) if m else {}
        except Exception as e:
            out = {"verdict": "无法判定", "reason": f"裁判失联({type(e).__name__})"}
        verdict = out.get("verdict")
        if verdict not in ("过", "不过", "无法判定"):
            verdict = "无法判定"
        return {"type": "judge_result", "player": player, "verdict": verdict,
                "reason": str(out.get("reason") or "")[:100]}

    def stt_transcribe(self, judge, audio_b64: str, fmt: str | None) -> str | None:
        """按住说话转写(PTT,房主裁定 2026-07-24;锁外调用,慢):音频→逐字稿。

        显式判定动作家族的成员:玩家按住「局长」键才收音、松手即停即传——与拍照
        判定同族,和已除名的"全程录音/监听"划清界限(那条禁令依然有效)。音频
        在本方法返回后即弃:不落盘、不进 episode、不进任何缓存;事件流里只有
        转写后的 say 文本,和打字发的一模一样。失败/空稿返回 None(调用方回
        502,App 端不丢录音可重试)。"""
        msgs = [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": audio_b64,
                                                    "format": fmt or "m4a"}},
            {"type": "text", "text": "转写"}]}]
        try:
            raw = judge.complete(STT_PROMPT, msgs)
        except Exception:
            return None
        text = (raw or "").strip()
        # 玩家一口气说不满 200 字;超长多半是模型跑偏在解释,截断保底
        return text[:200] if text else None

    def run_turn(self, body: dict | None = None) -> dict:
        """执行一拍(须在持有 self.lock 下调用):HTTP 手动驱动与服务端自驱共用。
        出服务端的一律是遮蔽版;原文只活在 episode(审计)和目标收件箱里。"""
        if isinstance(self.driver, ManualDriver):
            body = body or {}
            self.driver.pending = {"text": body.get("text", ""),
                                   "tool_use": body.get("tool_use", [])}
        line = self.engine.turn()
        red = self.route_private(line)
        self.recent.append(red)
        digest = self.state.digest(self.engine.time_left_min())
        # 主持沉默拍(调用失败/计费闸):桌上没发生任何事,桌友无从反应,不烧调用。
        # 计费闸到限时主持已走静默拍(host_silent),桌友这里再兜一道:即便主持这拍
        # 是靠最后一次额度成功的,到限后也不再放桌友进表(meter.exhausted)。
        bots = [] if line.get("host_silent") or self.meter.exhausted else self.bots
        t_bots = datetime.now().timestamp()
        if bots:
            # 桌友并行反应:串行时 7 座 × 每座数秒 = 一拍半分钟。收齐再统一
            # 入队(push_event 动共享账本,不在工作线程里做)。
            def _react(bot):
                try:
                    return bot.react(red, digest,
                                     inbox=self.inbox.get(bot.name, [])[-3:])
                except TypeError:
                    return bot.react(red, digest)

            with ThreadPoolExecutor(max_workers=len(bots)) as pool:
                reactions = list(pool.map(_react, bots))
            for evs in reactions:
                for ev in evs:
                    self.engine.push_event(ev)
        self.last_timing = {"host_ms": getattr(self.engine, "last_host_ms", 0),
                            "bots_ms": int((datetime.now().timestamp() - t_bots) * 1000)}
        if self.state.finished:
            self.recent.append(self.engine.run(max_turns=self.engine.marks["turns"]))
            persist.clear_snapshot(self)   # 收局清理快照:收完的局不该被 --resume 捞起
        else:
            # 每拍后原子落盘:进程中途死掉,从这个快照能续上整局(计时器恢复时作废)
            persist.write_snapshot(self)
        return red

    def start_autoloop(self, interval_s: float = 1.0) -> None:
        """服务端自驱回合环(App 时代的回合发动机):llm 驱动下起一条守护线程,
        turn_ready 就跑拍——驾驶舱页面从此不是发动机,电脑起完服可以合盖。"""
        def _loop():
            while not self.state.finished and not self.closed:
                _t.sleep(interval_s)
                try:
                    with self.lock:
                        if not self.state.finished and not self.closed and self.engine.turn_ready():
                            self.run_turn()
                except Exception:
                    traceback.print_exc()  # 单拍失败不杀发动机;主持断线已有沉默拍兜底
        threading.Thread(target=_loop, daemon=True).start()

    def player_view(self, me: str, depth: int = 6) -> dict:
        """一台手机该看见的东西,别的一个字都不给(房主裁定 2026-07-20)。

        实测三桌的私密性结论全部作废,因为模拟台只有 /api/state 一个口,
        而那是驾驶舱。玩家为了看「到我了吗」被迫读它,顺带撞见 inbox_counts、
        show 的收件人、random 的结果——「我没法不看到」(小圆)。泄漏面是
        测试工具带进去的,不是产品的。

        三条设计注记:
        · 必须带进度(回合号/turn_ready/焦点),否则玩家还得回驾驶舱,等于没堵;
        · 别人的 to=局长 定向发言只显示「有人跟主持说了句话」,不给内容;
        · 不含 tool_use / results / inbox_counts / 别人的收件箱。
        """
        digest = self.state.digest(self.engine.time_left_min())
        ask = self.state.open_ask
        recent = []
        for line in self.recent[-depth:]:
            if line.get("episode_summary"):
                continue
            shown = [r["result"].get("display") for r in (line.get("results") or [])
                     if r.get("ok") and isinstance(r.get("result"), dict)
                     and r["result"].get("visibility") == "全场公开"
                     and r["result"].get("display")]
            table = []
            for e in (line.get("events_in") or []):
                if e.get("type") == "duel_result":
                    # 对决揭晓没有 player 字段,但它是全场该看见的结果,不能被
                    # 「无 player 就跳过」滤掉——手机上要演这一下。
                    table.append({"type": "duel_result", "winner": e.get("winner"),
                                  "loser": e.get("loser"), "reason": e.get("reason"),
                                  "vs": e.get("vs")})
                    continue
                who = e.get("player")
                if not who:
                    continue
                item = {"player": who, "type": e.get("type")}
                if e.get("type") == "say":
                    if e.get("to") == "局长" and who != me:
                        item["note"] = "跟主持说了句话"      # 听得见有人开口,听不见内容
                    elif e.get("text"):
                        item["text"] = e["text"]
                elif e.get("type") == "challenge":
                    # 开牌是全场判定时刻:谁开的+被开那口叫价都是公开信息(桌上喊出来的)
                    if e.get("bid"):
                        item["bid"] = e["bid"]
                elif e.get("value"):
                    item["value"] = e["value"]
                table.append(item)
            recent.append({"turn": line.get("turn"), "host": line.get("text", ""),
                           "shown": shown, "table": table})
        # 系统级炸铃:铃是公开广播,人人要响,故 bell 进每台手机的 view(不遮蔽)。
        # 带上 server_now(服务器当前 epoch)让客户端算钟差,把 bell.at 换算到本地钟
        # 精确定时齐响(消灭轮询 900ms 抖动)。铃过期 3 秒以上不再下发(响过就撤)。
        now = _t.time()
        bell = self.state.pending_bell
        bell_out = ({"at": bell["at"], "fx": bell["fx"]}
                    if bell and now - bell["at"] < 3.0 else None)
        return {
            "you": me,
            "round": digest.get("round"), "turn": self.engine.marks["turns"],
            "turn_ready": self.engine.turn_ready(), "focus": digest.get("focus"),
            "finished": self.state.finished,
            "time_left_min": digest.get("time_left_min"),
            "now_playing": digest.get("now_playing"),  # 音乐是全场公开的:现实里人人听得见
            "server_now": now,   # 服务器当前 epoch:客户端据此算钟差,炸铃本地精确定时
            "bell": bell_out,    # 系统级炸铃 {at, fx}:全桌手机换算后齐响那声"停!"
            # 对决状态:玩家端只看到 vs 与 drawn 布尔(枪响了没),拔枪时点不出服务端
            "duel": digest.get("duel"),
            # 拍照判定:只有被点名的人看到出题(别人只从主持嘴里听到)
            "photo_request": (self.state.pending_photo["prompt"]
                              if self.state.pending_photo
                              and self.state.pending_photo["player"] == me else None),
            "audio_request": (self.state.pending_audio["prompt"]
                              if self.state.pending_audio
                              and self.state.pending_audio["player"] == me else None),
            "scores": digest.get("scores"), "scene_objects": digest.get("scene_objects"),
            "now_playing": digest.get("now_playing"),   # 手机上要显示正在放的歌
            "timer_running": digest.get("timer_running"),
            # 我自己的骰盅:rolled(点数)只给本人——摇过常驻显示,大话骰全程盯着吹牛。
            # 开牌后带 challenged_by/bid(App 据此显示"已开牌,等局长清算")
            "my_prop": ({"kind": me_prop["kind"], "count": me_prop["count"],
                         "rolled": me_prop.get("rolled"),
                         **({"challenged_by": me_prop["challenged_by"],
                             "bid": me_prop.get("bid")}
                            if me_prop.get("challenged_by") else {})}
                        if (me_prop := self.state.props.get(me)) else None),
            # 全桌骰盅状态:谁有盅、摇没摇(布尔)——旁人看得到动作,看不到点数。
            # 开牌标(challenged_by/bid)是公开信息(桌上拍桌喊出来的),全桌可见——
            # App 轮询见它从无到有就放「⚡ 开牌!」横幅+重触感;点数依旧不出本人。
            "cups": [{"player": p, "rolled": pr.get("rolled") is not None,
                      **({"challenged_by": pr["challenged_by"],
                          "bid": pr.get("bid")}
                         if pr.get("challenged_by") else {})}
                     for p, pr in self.state.props.items()],
            # 额头牌=人身上的道具:给出**别人**的牌(点人看牌),自己那张永远缺席——
            # 可见性反转在服务端成立,客户端天然拿不到自己的词
            "foreheads": {p: t for p, t in self.state.foreheads.items() if p != me},
            # 我的牌(牌卡道具):本人常驻区,kind+content+status 全给——卧底词/密令/
            # 毒杯号一直摊在自己手机上盯着,不是划过去就没的私件流水。
            "my_cards": [{"kind": c["kind"], "content": c["content"], "status": c["status"]}
                         for c in self.state.cards.get(me, [])],
            # 全桌的牌:谁持什么类型什么状态(无内容)——桌上知道谁手里有张什么牌;
            # 唯 revealed(翻公开)的牌把 content 一并给出(揭晓了就是公开信息)。
            "table_cards": [
                {"player": p, "kind": c["kind"], "status": c["status"],
                 **({"content": c["content"]} if c["status"] == "revealed" else {})}
                for p, cs in self.state.cards.items() for c in cs],
            "inbox": self.inbox.get(me, [])[-8:],          # 只有自己的
            "open_ask": ({"prompt": ask["prompt"], "asked": ask["asked"],
                          "options": ask["options"]} if ask else None),  # 不含 answers
            "recent": recent,
        }

    def snapshot(self) -> dict:
        return {
            "players": self.state.players,
            "room_code": self.room_code,     # 驾驶舱开局后显示、轮询带它
            "digest": self.state.digest(self.engine.time_left_min()),
            "finished": self.state.finished,
            "marks": dict(self.engine.marks),
            # 计费闸仪表:台面可见(玩家 /api/view 不暴露)。gated=已合闸转静默拍
            "budget": {"used": self.meter.used, "limit": self.meter.limit,
                       "gated": self.meter.exhausted},
            # 感知档必须在服务端落地。只裁 turn 的 events_in 是不够的:主持轮询
            # /api/state 就能读到原文,约束退化成"靠它自觉",而且污染在流水里看不出来。
            "pending_events": self.engine._perceive(list(self.engine.event_queue)),
            "recent_turns": self.recent[-12:],
            "inbox_counts": {k: len(v) for k, v in self.inbox.items() if v},
            "clamps": self.engine.tools.clamp_log[-5:],
            "episode_path": str(self.episode_path),
            "driver": self.driver_kind,
            "bots": self.bot_names,
            "join_base": self.join_base,   # 入座链接前缀(局域网地址,手机能打开的那个)
            "host_perception": self.state.host_perception,
            "turn_ready": self.engine.turn_ready(),
            "last_timing": self.last_timing,
            # 桌友调用失败数:静默降级会让 key 错表现成「bot 好闷」,得摆到台面上
            "bot_errors": {b.name: {"count": b.errors, "last": b.last_error}
                           for b in self.bots if getattr(b, "errors", 0)},
            # 主持沉默拍同理:错误不进游戏,只进台面
            "host_errors": ({"count": self.engine.marks["host_errors"],
                             "streak": self.engine.host_error_streak,
                             "last": self.engine.last_host_error}
                            if self.engine.marks["host_errors"] else None),
        }


class Lobby:
    """大厅态(开放入座房):房主什么都不填就开房拿码,朋友自己进来报名,人齐了锁定开打。

    开房这一下**不建引擎、不烧 LLM**——局还没开,只占一个房间码 + 一份待锁的 cfg。
    朋友经 /api/join 自己报名入座(名字去空格、重名驳回、上限 MAX_PLAYERS);房主
    (开房那台,凭 host_token 或开房时的 device_id 识别)经 /api/lock 用最终名单
    构建 Session。大厅态不落盘:没开打丢了就重开(锁定后才走既有 persist 流程)。
    """

    def __init__(self, code: str, cfg: dict, host_token: str,
                 host_device: str = "", join_base: str = "") -> None:
        self.room_code = code
        self.cfg = cfg                 # 待锁 cfg(除 players 外的开房参数,含 bots)
        self.host_token = host_token
        self.host_device = host_device  # 开房那台的 device_id(host_token 之外的第二把认人钥匙)
        self.roster: list[dict] = []    # [{"name":..., "device_id":...}] 按入座先后
        self.join_base = join_base
        self.last_active = _t.time()
        self.started = False            # 锁定过渡标记:置真即不再收人(引擎在 Hub.lock 里建)
        # 大厅态拍照读场(输入侧去打字化,房主裁定 2026-07-24):房主开房那一拍拍张现场照,
        # 走既有视觉裁判链路析出这三样,存这里;lock 时并入 Session(手填字段优先,拍照只填空缺)。
        # 视觉不可用(无 key)时这三样留空,大厅照常锁定开打——拍照是增强不是门槛。
        self.occasion_guess = ""        # 场合猜测(生日/团建/情侣……)
        self.scene_brief = ""           # 一句场景速写
        self.scene_objects: list[str] = []  # 认出的可入局实物清单
        self.lock = threading.Lock()

    def names(self) -> list[str]:
        return [r["name"] for r in self.roster]

    def is_host(self, host_token: str | None, device_id: str | None) -> bool:
        """房主认人:host_token 对上,或 device_id 是开房那台。二者任一即可。"""
        if host_token and host_token == self.host_token:
            return True
        if device_id and self.host_device and str(device_id)[:64] == self.host_device:
            return True
        return False

    def add_seat(self, name: str, device_id: str | None) -> tuple[int, str] | None:
        """报名入座(须在锁内经 Hub 调用):成功返回 None,失败返回 (http_code, msg)。
        名字去空格;重名驳回(除非同名+同设备=重连,放行不重复加);满员驳回。"""
        name = (name or "").strip()
        if not name:
            return (400, "报名要填个名字")
        name = name[:24]                       # 超长名字截断,免得撑爆台面
        dev = str(device_id)[:64] if device_id else ""
        with self.lock:
            if self.started:
                return (409, "本局已开打,进不来了")
            for r in self.roster:
                if r["name"] == name:
                    # 同名 + 同设备 = 断线重连,放行(不重复加);同名换设备 = 撞名,驳回
                    if dev and r.get("device_id") == dev:
                        return None
                    return (409, f"“{name}”这个名字已经有人用了,换一个")
            if len(self.roster) >= MAX_PLAYERS:
                return (409, f"人满了(上限 {MAX_PLAYERS} 人),进不来了")
            self.roster.append({"name": name, "device_id": dev})
            return None

    def scene_photo(self, image_b64: str, media_type: str | None) -> dict:
        """大厅态拍照读场(锁外调用,慢):现场照 → {occasion_guess 场合猜测,
        objects 实物清单,brief 场景速写},存进大厅待 lock 时并入 Session。
        走既有视觉裁判链路(与 Session.scene_photo 同款 transport),provider/视觉模型
        取开房 cfg(不带就落 YAPPA_PROVIDER/YAPPA_MODEL 环境默认)。视觉不可用(无 key)
        时返回 {"error": ...} 且不改动大厅态——拍照是增强不是门槛,大厅照常锁定开打。"""
        import re as _re

        from .transports import vision_model_for
        provider = self.cfg.get("provider") or _default_provider()
        host_model = self.cfg.get("host_model") or _default_model()
        model = vision_model_for(provider, host_model)
        sys_p = ('你是聚会现场侦察员。从照片提取可入游戏的信息,只输出 JSON:'
                 '{"occasion_guess": "一句场合猜测(生日/团建/情侣约会/朋友小聚/办公室……,拿不准就写最像的)",'
                 '"objects": ["现场实物,可作道具的,如 瓶子/冰块/抱枕/投影"],'
                 '"brief": "一句场景速写(在哪/什么氛围/有什么可玩的)"}')
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": media_type or "image/jpeg",
                                         "data": image_b64}},
            {"type": "text", "text": "提取"}]}]
        try:
            judge = make_transport(provider, model)
            raw = judge.complete(sys_p, msgs)
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            out = json.loads(m.group(0)) if m else {}
        except Exception as e:
            # 无 key/网络断/模型不存在都走这里:明确报错,大厅态一个字段都不动
            return {"error": f"视觉裁判不可用({type(e).__name__}):{e}。可手填场合,大厅照常锁定开打"}
        objs = [str(o) for o in (out.get("objects") or []) if str(o).strip()][:20]
        brief = str(out.get("brief") or "").strip()[:120]
        occ = str(out.get("occasion_guess") or "").strip()[:40]
        with self.lock:
            # 多角度侦察(房主实测:场子比一帧大):实物并集、速写**累积拼接**
            # (不许后一张覆盖前一张——桌面照和全景照各说各的,都要留);
            # 场合猜测取最新非空(多拍通常越拍越准)。上限 5 张防视觉调用刷钱。
            self.scene_photos = getattr(self, "scene_photos", 0) + 1
            if self.scene_photos > 5:
                return {"error": "侦察最多 5 张,已经够局长看清场子了"}
            merged = list(dict.fromkeys([*self.scene_objects, *objs]))
            self.scene_objects = merged
            if brief and brief not in self.scene_brief:
                self.scene_brief = (self.scene_brief + ";" + brief).strip(";")[:240]
            if occ:
                self.occasion_guess = occ
        return {"occasion_guess": occ, "objects": objs, "brief": brief,
                "scene_objects": merged, "photos": self.scene_photos,
                "scene_brief": self.scene_brief}


class Hub:
    """多局并发:一台服务器同时跑多桌,dict[room_code, Session]。
    /api/* 用 pick() 定位房间:带 room 参数指定,不带则默认唯一活跃房间(向后兼容),
    多房间不带 room 报 409。收局/闲置(2h 无活动)的房间从字典踢出,episode 文件保留。
    大厅态(未锁定的开放入座房)另存 lobbies,锁定时转成 Session 挪进 rooms。"""

    def __init__(self, out_dir: Path, join_base: str = "") -> None:
        self.rooms: dict[str, Session] = {}
        self.lobbies: dict[str, Lobby] = {}   # 未锁定的大厅房(开放入座,尚未建引擎)
        self.out_dir = out_dir
        self.join_base = join_base
        self.lock = threading.Lock()   # 保护 rooms/lobbies 字典的增删

    def _sweep(self, include_finished: bool) -> None:
        """回收房间(须持 self.lock):闲置(>2h)一律回收;收局房仅在开新局时回收——
        平时留着,好让「单房时不带 room 默认命中」对刚收的局也成立(向后兼容)。"""
        now = _t.time()
        doomed = [c for c, s in self.rooms.items()
                  if (include_finished and s.state.finished)
                  or (now - s.last_active > IDLE_LIMIT_S)]
        for code in doomed:
            s = self.rooms.pop(code)
            with s.lock:
                s.closed = True             # autoplay 线程见到即退出(随房间生命周期)
                if not s.state.finished:
                    s.state.finished = True  # 闲置回收:关掉引擎与文件(快照保留可 --resume)
                    try:
                        s.engine._ep.close()
                    except (ValueError, OSError):
                        pass
        # 大厅房闲置(>2h 无人 join/开打)一并回收:没建引擎没落盘,直接丢弃
        for code in [c for c, lo in self.lobbies.items()
                     if now - lo.last_active > IDLE_LIMIT_S]:
            self.lobbies.pop(code, None)

    def pick(self, room: str | None):
        """定位房间 → (session, code, err)。err 为 None=成功;否则 (http_code, msg)。
        不回收收局房(单房默认要用它);只顺手清闲置房(2h 门槛,常态不触发)。"""
        with self.lock:
            self._sweep(include_finished=False)
            if room:
                s = self.rooms.get(room)
                if s is None:
                    return None, None, (404, f"房间 {room} 不存在或已回收")
                s.last_active = _t.time()
                return s, room, None
            items = list(self.rooms.items())
            if not items:
                return None, None, (409, "先 /api/start 开局")
            if len(items) == 1:
                code, s = items[0]
                s.last_active = _t.time()
                return s, code, None
            codes = "、".join(sorted(c for c, _ in items))
            return None, None, (409, f"有 {len(items)} 个活跃房间,请带 room 指定(可用:{codes})")

    def start(self, cfg: dict) -> dict:
        # 开局口令闸(公网认证裁定 2026-07-23):隧道公开后,/api/start 是唯一会
        # 烧 LLM 钱的入口——陌生人扫到就能白嫖开局。设 ZAKZOK_START_KEY 后必须
        # 对上口令;入座/事件不设闸(房间码即门票,朋友零摩擦)。口令即刻弹出
        # cfg,不进 Session/快照/episode 任何留痕面。
        required = os.environ.get("ZAKZOK_START_KEY", "")
        offered = str(cfg.pop("key", "") or "")
        if required and offered != required:
            raise ValueError("开局口令不对(服务器设了开局口令,向房主要)")
        with self.lock:
            self._sweep(include_finished=True)   # 开新局时清收局/闲置房,episode 文件保留
            code = gen_room_code(set(self.rooms) | set(self.lobbies))
        players = [p.strip() for p in cfg.get("players", []) if p.strip()]
        if not players:
            # —— 大厅态:房主什么都不填直接开房拿码 —— #
            # 此刻不建引擎、不烧 LLM(局未开);朋友自己 join 报名,房主 lock 才开打。
            # 开局口令闸在上面照旧管住这一下(公网防白嫖开房)。
            host_token = secrets.token_hex(8)
            host_device = str(cfg.get("device_id", "") or "")[:64]
            lobby_cfg = {k: v for k, v in cfg.items() if k not in ("players", "device_id")}
            lobby = Lobby(code, lobby_cfg, host_token, host_device, join_base=self.join_base)
            with self.lock:
                self.lobbies[code] = lobby
            return {"room_code": code, "host_token": host_token, "lobby": True,
                    "started": False, "roster": [],
                    "limits": {"min_players": MIN_PLAYERS, "max_players": MAX_PLAYERS}}
        session = Session(
            players=players,
            minutes=int(cfg.get("minutes", 30)),
            wildness=int(cfg.get("wildness", 6)),
            objects=[o.strip() for o in cfg.get("objects", []) if o.strip()],
            driver_kind=cfg.get("driver", "manual"),
            out_dir=self.out_dir,
            bots=cfg.get("bots") or {},
            # 不带就落到 Session 的环境变量默认(YAPPA_PROVIDER/YAPPA_MODEL)
            provider=cfg.get("provider"),
            host_model=cfg.get("host_model"),
            seat_model=cfg.get("seat_model"),
            score_style=cfg.get("score_style", "自动"),
            host_perception=cfg.get("host_perception", "转写"),
            playlist=cfg.get("playlist") or [],
            occasion=cfg.get("occasion", ""), scene_brief=cfg.get("scene_brief", ""),
            max_llm_calls=cfg.get("max_llm_calls"),   # None=按环境变量/默认 500
        )
        session.join_base = self.join_base
        session.room_code = code
        session.autoplay = bool(cfg.get("autoplay"))
        session.autoplay_interval_s = float(cfg.get("autoplay_interval_s", 1.0))
        if cfg.get("autoplay") and cfg.get("driver") == "llm":
            # 服务端自驱:回合发动机进服务器,驾驶舱页面可关、电脑可合盖
            session.start_autoloop(session.autoplay_interval_s)
        with self.lock:
            self.rooms[code] = session
        return session.snapshot()

    def _resolve(self, code: str | None):
        """大厅/开打房通用定位:带码精确命中;不带码且全场唯一一个(大厅或开打)则默认
        命中(单房向后兼容,大厅态开发自测也不必逐次敲码)。返回 (lobby, session)。"""
        with self.lock:
            self._sweep(include_finished=False)
            if code:
                return self.lobbies.get(code), self.rooms.get(code)
            all_codes = list(self.lobbies) + list(self.rooms)
            if len(all_codes) == 1:
                c = all_codes[0]
                return self.lobbies.get(c), self.rooms.get(c)
            return None, None

    def join(self, code: str | None, name: str, device_id: str | None) -> tuple[dict, int]:
        """报名入座:大厅态进花名册;已开打的房只放同名(+同设备)重连,否则驳回。"""
        lobby, session = self._resolve(code)
        if lobby is not None:
            lobby.last_active = _t.time()
            err = lobby.add_seat(name, device_id)
            if err:
                return {"error": err[1]}, err[0]
            return {"ok": True, "you": (name or "").strip()[:24], "started": False,
                    "roster": lobby.names()}, 200
        if session is not None:
            # 座位已封闭:同名(且设备没被别人占)= 断线重连,放行;否则「本局已开打」
            nm = (name or "").strip()[:24]
            if nm in session.state.players:
                with session.lock:
                    dev = str(device_id)[:64] if device_id else ""
                    bound = session.device_map.get(nm)
                    if dev and bound and bound != dev:
                        return {"error": f"“{nm}”这个座位已被其他设备占用"}, 409
                    session.bind_device(nm, device_id)
                return {"ok": True, "you": nm, "started": True,
                        "room_code": session.room_code}, 200
            return {"error": "本局已开打,不能再入座(同名同机可重连)"}, 409
        return {"error": f"房间 {code or ''} 不存在或已回收"}, 404

    def lobby_state(self, code: str | None) -> tuple[dict, int]:
        """大厅轮询口:大厅态给 roster;已锁定则回 started=true,App 据此切游戏页。"""
        lobby, session = self._resolve(code)
        if lobby is not None:
            lobby.last_active = _t.time()
            return {"lobby": True, "started": False, "room_code": lobby.room_code,
                    "roster": lobby.names(),
                    "limits": {"min_players": MIN_PLAYERS, "max_players": MAX_PLAYERS}}, 200
        if session is not None:
            return {"started": True, "room_code": session.room_code,
                    "players": list(session.state.players)}, 200
        return {"error": f"房间 {code or ''} 不存在或已回收"}, 404

    def lobby_scene(self, code: str | None, host_token: str | None,
                    device_id: str | None, image_b64: str | None,
                    media_type: str | None) -> tuple[dict, int]:
        """大厅态拍照读场:房主开房那一拍传现场照,视觉链路析出场合/实物/速写存进大厅。
        只有房主(host_token/开房 device)能拍;已开打的房不收(读场只在大厅态)。
        视觉不可用(无 key)返回 502+明确提示,大厅态不变(拍照是增强不是门槛)。"""
        lobby, session = self._resolve(code)
        if lobby is None:
            if session is not None:
                return {"error": "本局已开打,拍照读场只在大厅态(开打前)可用"}, 409
            return {"error": f"房间 {code or ''} 不存在或已回收"}, 404
        if not lobby.is_host(host_token, device_id):
            return {"error": "只有房主能拍场子(开房那台)"}, 403
        if not image_b64:
            return {"error": "缺 image_b64"}, 400
        lobby.last_active = _t.time()
        out = lobby.scene_photo(image_b64, media_type)   # 锁外(慢),不占 Hub 字典锁
        return (out, 502) if "error" in out else (out, 200)

    def lock_room(self, code: str | None, host_token: str | None,
                  device_id: str | None) -> tuple[dict, int]:
        """房主锁定开打:用最终名单构建 Session 引擎、autoplay 起、座位封闭。
        非房主驳回 403;人不够 2 驳回;并入开房时配的 bots。已锁过则幂等回 started。"""
        lobby, session = self._resolve(code)
        if lobby is None:
            if session is not None:            # 已经锁过 → 幂等:当作已开打
                return {"started": True, "room_code": session.room_code}, 200
            return {"error": f"房间 {code or ''} 不存在或已回收"}, 404
        with lobby.lock:
            if not lobby.is_host(host_token, device_id):
                return {"error": "只有房主能开打(开房那台)"}, 403
            if lobby.started:
                return {"error": "正在开打中"}, 409
            roster = list(lobby.roster)
            cfg = dict(lobby.cfg)
            code = lobby.room_code
            # 拍照读场结果一并取出(锁内,防与并发拍照竞态):lock 时并入 Session,
            # 但**手填优先**——房主开房参数里填了的字段不被拍照覆盖,拍照只填空缺。
            scene = {"occasion_guess": lobby.occasion_guess,
                     "scene_brief": lobby.scene_brief,
                     "scene_objects": list(lobby.scene_objects)}
            lobby.started = True             # 占位:防并发双锁;建局失败下面回滚
        names = [r["name"] for r in roster]
        bots = cfg.get("bots") or {}
        # bots 是 AI 座位:并入最终名单(不在花名册里的 bot 名补成座位)
        players = names + [b for b in bots if b not in names]
        # —— 拍照读场并入(手填优先,拍照只填空缺)—— #
        # occasion/scene_brief 是单值:房主手填了就用手填,没填才用拍照猜测;
        # objects 是清单:手填的排前,拍照认出的去重补在后(两边都不丢)。
        occasion = (cfg.get("occasion", "") or "").strip() or scene["occasion_guess"]
        scene_brief = (cfg.get("scene_brief", "") or "").strip() or scene["scene_brief"]
        objects = [o.strip() for o in cfg.get("objects", []) if o.strip()]
        objects = list(dict.fromkeys([*objects, *scene["scene_objects"]]))
        try:
            if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS:
                raise ValueError(f"开打要 {MIN_PLAYERS}–{MAX_PLAYERS} 人(现在 {len(players)} 人)")
            session = Session(
                players=players,
                minutes=int(cfg.get("minutes", 30)),
                wildness=int(cfg.get("wildness", 6)),
                objects=objects,
                driver_kind=cfg.get("driver", "manual"),
                out_dir=self.out_dir,
                bots=bots,
                provider=cfg.get("provider"),
                host_model=cfg.get("host_model"),
                seat_model=cfg.get("seat_model"),
                score_style=cfg.get("score_style", "自动"),
                host_perception=cfg.get("host_perception", "转写"),
                playlist=cfg.get("playlist") or [],
                occasion=occasion, scene_brief=scene_brief,
                max_llm_calls=cfg.get("max_llm_calls"),
            )
        except ValueError as e:
            lobby.started = False            # 回滚:人不够时房主补人再锁
            return {"error": str(e)}, 400
        session.join_base = self.join_base
        session.room_code = code
        session.autoplay = bool(cfg.get("autoplay"))
        session.autoplay_interval_s = float(cfg.get("autoplay_interval_s", 1.0))
        # 花名册里带过来的设备锚点顺手绑定(走既有 bind_device,落 episode 审计线)
        with session.lock:
            for r in roster:
                if r.get("device_id"):
                    session.bind_device(r["name"], r["device_id"])
        if cfg.get("autoplay") and cfg.get("driver") == "llm":
            session.start_autoloop(session.autoplay_interval_s)
        with self.lock:
            self.rooms[code] = session
            self.lobbies.pop(code, None)     # 大厅转正:从待锁字典移除
        return session.snapshot(), 200


def lan_host() -> str:
    """本机在局域网里的地址。手机连的是 Wi-Fi,localhost 打不开房主的机器——
    入座链接必须带这个 IP 才有意义。取不到就退回 localhost(单机自测仍可用)。"""
    import socket
    cands = []
    try:  # 先枚举本机所有 IPv4:出口路由那招在有 TUN/VPN 的机器上会返回隧道地址
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            cands.append(info[4][0])
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # 不发包,只让内核选出出口网卡
        cands.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    # 家用 Wi-Fi 绝大多数是 192.168.*,优先;其次 10.*;172.16-31.* 常是 VPN/容器,放最后
    for pref in ("192.168.", "10."):
        for ip in cands:
            if ip.startswith(pref):
                return ip
    return next((ip for ip in cands if not ip.startswith("127.")), "localhost")


HTML_PATH = Path(__file__).with_name("sim_ui.html")

# 根路径落地页(用户版门面):玩家永远在 App 里进桌,这页只负责指路+不吓人。
LANDING_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZAKZOK</title><style>
body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;
justify-content:center;background:#0e0f14;color:#eef;font-family:-apple-system,'PingFang SC',sans-serif}
h1{font-size:56px;letter-spacing:4px;color:#f5c518;margin:0}
p{color:#99a;font-size:17px;margin:14px 0 0;text-align:center;line-height:1.7;padding:0 24px}
</style></head><body><h1>ZAKZOK</h1>
<p>局长在等你入座。<br>打开 ZAKZOK App,输入房间码和座位名即可进桌。</p>
</body></html>"""
PLAY_PATH = Path(__file__).with_name("play_ui.html")   # 玩家手机页


class Handler(BaseHTTPRequestHandler):
    hub: Hub  # 类属性注入

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器刷新/关页面时连接已断,写不回去是正常的,不是错误。
            # 不吞掉的话下游那个 500 兜底会二次写、二次爆炸。
            pass

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, *a) -> None:  # 静默访问日志
        pass

    def _seat_param(self, session) -> str:
        """取 ?player= 座位名。http.server 按 latin-1 解请求行,裸中文会变成
        å°å——转回 utf-8 再认一次,免得中文座位名默认用不了。"""
        from urllib.parse import parse_qs, urlparse
        player = (parse_qs(urlparse(self.path).query).get("player") or [""])[0]
        if session and player not in session.inbox:
            try:
                player = player.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        return player

    def _room_param(self, body: dict | None = None) -> str | None:
        """取房间码:query ?room= 优先,其次 POST body 的 room 字段;都没有返回 None。"""
        from urllib.parse import parse_qs, urlparse
        q = (parse_qs(urlparse(self.path).query).get("room") or [""])[0]
        if q:
            return q
        if isinstance(body, dict) and body.get("room"):
            return str(body["room"])
        return None

    def _pick(self, body: dict | None = None):
        """定位房间,失败时直接回错误并返回 None(调用方据此提前结束)。"""
        s, _code, err = self.hub.pick(self._room_param(body))
        if err:
            self._json(err[0], {"error": err[1]})
            return None
        return s

    def _tts(self) -> None:
        """GET /api/tts?room=X&line=N[&voice=V]:把主持第 N 拍的话合成为音频返回。

        · 按需合成:不自动合成每一拍——只有 App/驾驶舱来拉这条才烧钱;
        · 不带 line 默认最新一条有词的主持拍(App 轮询"念最新那句"最顺手);
        · (room, line) 内存缓存(Session 即房间),同一句不重复烧钱;
        · TTS 未配置(无 key)回 404+说明 JSON,不报错不崩——ready-to-plug,
          房主接上 key 这条口子自动活;合成失败回 502,绝不拖垮主持拍。
        TTS 是出口:只念主持已说出口的字,不含任何 ASR/录音。"""
        from urllib.parse import parse_qs, urlparse
        s = self._pick()
        if s is None:
            return
        if not tts.configured():
            self._json(404, {"error": "TTS 未接入(设 TTS_API_KEY 或 DASHSCOPE_API_KEY 即通)"})
            return
        q = parse_qs(urlparse(self.path).query)
        line_q = (q.get("line") or [""])[0]
        voice = (q.get("voice") or [""])[0] or None
        with s.lock:
            # 只念主持的话(text);episode_summary 是收局账单,不是台词
            spoken = [ln for ln in s.recent
                      if (ln.get("text") or "").strip() and not ln.get("episode_summary")]
        if line_q:
            try:
                n = int(line_q)
            except ValueError:
                self._json(400, {"error": f"line 需是回合号整数(收到 {line_q!r})"})
                return
            line = next((ln for ln in spoken if ln.get("turn") == n), None)
        else:
            line = spoken[-1] if spoken else None
        if line is None:
            self._json(404, {"error": f"没有可念的主持拍(line={line_q or '最新'})"})
            return
        key = (line.get("turn"), voice or "")
        audio = s.tts_cache.get(key)
        if audio is None:
            try:
                audio = tts.synthesize(line["text"], voice)   # 锁外合成(慢),局照跑
            except tts.TTSError as e:
                self._json(502, {"error": str(e)})
                return
            s.tts_cache[key] = audio
        try:
            self.send_response(200)
            self.send_header("Content-Type", tts.TTS_MIME)
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 拉音频的那头关页面了,正常,不是错误

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            # 公网上线后根路径是产品的门面:给玩家落地页,不给开发驾驶舱
            #(真机实测:房主自己开域名都以为进错了地方)。驾驶舱挪 /cockpit。
            body = LANDING_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/cockpit") or self.path.startswith("/play"):
            # /play[?player=X] = 玩家页(手机);/cockpit = 驾驶舱(房主)。两页两套视角,
            # 玩家页只吃 /api/view,拿不到 tool_use/results/inbox_counts。
            body = (PLAY_PATH if self.path.startswith("/play") else HTML_PATH).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path.startswith("/api/inbox") or self.path.startswith("/api/view"):
            s0 = self._pick()
            if s0 is None:
                return
            player = self._seat_param(s0)
            if player not in s0.inbox:
                self._json(400, {"error": f"未知座位: {player}"}); return
            if self.path.startswith("/api/view"):
                # 玩家视图:一台手机该看见的东西。带进度(否则玩家还得回驾驶舱),
                # 不含 tool_use / results / inbox_counts / 别人的收件箱 / 计费闸。
                self._json(200, s0.player_view(player))
            else:
                self._json(200, {"player": player, "inbox": s0.inbox[player][-8:]})
            return
        if self.path.startswith("/api/tts"):
            self._tts()
            return
        if self.path.startswith("/api/lobby"):
            # 大厅轮询口:App 开房/入座后停在大厅页,靠它拉 roster、发现 started 后切游戏页
            out, status = self.hub.lobby_state(self._room_param())
            self._json(status, out)
            return
        if self.path.startswith("/api/state"):
            s, _code, err = self.hub.pick(self._room_param())
            if err and not self.hub.rooms:
                # 无房:保持旧的 no_session 契约(前端据此显示未开局)
                self._json(200, {"no_session": True,
                           "limits": {"min_players": MIN_PLAYERS, "max_players": MAX_PLAYERS}})
                return
            if err:
                self._json(err[0], {"error": err[1]}); return
            self._json(200, s.snapshot())
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self._body()   # 只读一次(rfile 读完即空);room 从 query 或此 body 取
            # 路由只看路径,不看 query。客户端多桌模式会给每个请求挂 ?room=XXXX
            # (App.js 的 api() 除 /api/start 外一律追加),精确匹配 self.path 会
            # 让这些请求全部 404——而 GET 侧用的是 startswith,轮询照常。症状就是
            # 玩家画面一切正常、按什么都没反应,填了房间码的桌子等于全员失声。
            path = self.path.split("?", 1)[0]
            if path == "/api/start":
                self._json(200, self.hub.start(body))
                return
            if path == "/api/join":
                # 报名入座:大厅态进花名册(去空格/重名/满员在 Lobby 里判);
                # 已开打的房只放同名+同设备重连。不走 _pick(大厅房不是 Session)。
                out, status = self.hub.join(self._room_param(body),
                                            body.get("name"), body.get("device_id"))
                self._json(status, out)
                return
            if path == "/api/lobby_scene":
                # 大厅态拍照读场:房主开房那一拍传现场照,视觉链路析出场合/实物/速写存进大厅,
                # lock 时并入 Session。只有房主能拍;无 key 明确报错且不挡后续锁定。
                # 不走 _pick(大厅房不是 Session)。
                out, status = self.hub.lobby_scene(
                    self._room_param(body), body.get("host_token"), body.get("device_id"),
                    body.get("image_b64"), body.get("media_type"))
                self._json(status, out)
                return
            if path == "/api/lock":
                # 房主开打:用最终名单建引擎、autoplay 起、座位封闭。非房主 403。
                out, status = self.hub.lock_room(self._room_param(body),
                                                 body.get("host_token"), body.get("device_id"))
                self._json(status, out)
                return
            s = self._pick(body)
            if s is None:
                return
            if path == "/api/event":
                ev = dict(body)
                ev.pop("room", None)   # room 是路由参数,不进事件流
                dev = ev.pop("device_id", None)  # 设备锚点是元信息,不进事件流
                if dev:
                    with s.lock:
                        s.bind_device(ev.get("player"), dev)
                if ev.get("type") == "roll":
                    # 摇盅是玩家的「玩」动作,不是普通事件:走 roll_cup 校验+出点+私发+对账,
                    # 点数绝不能明文进事件流(那样就等于局长替玩家摇了)。
                    with s.lock:
                        out = s.roll_cup(ev.get("player"))
                    self._json(200 if out.get("ok") else 409, out)
                    return
                if ev.get("type") == "challenge":
                    # 开牌是判定时刻(与快枪手的「拔」同类),不是普通事件:走 challenge
                    # 校验(持已摇盅、一局一开)+ 锁全桌盅 + 公开事件。叫价博弈本身
                    # 永远留在嘴上不进系统(宪法),这里只收「谁开的+被开那口叫价」。
                    with s.lock:
                        out = s.challenge(ev.get("player"), ev.get("bid"))
                    self._json(200 if out.get("ok") else 409, out)
                    return
                with s.lock:
                    s.engine.push_event(ev)
                self._json(200, {"queued": ev})
                return
            if path == "/api/scene":
                if not body.get("image_b64"):
                    self._json(400, {"error": "缺 image_b64"})
                    return
                out = s.scene_photo(body["image_b64"], body.get("media_type"))
                self._json(200 if "error" not in out else 502, out)
                return
            if path == "/api/audio":
                player = body.get("player")
                with s.lock:
                    pend = s.state.pending_audio
                if not pend:
                    self._json(409, {"error": "没有进行中的录音判定"})
                    return
                if pend["player"] != player:
                    self._json(403, {"error": f"这单判定点的是 {pend['player']},不是 {player}"})
                    return
                if not body.get("audio_b64"):
                    self._json(400, {"error": "缺 audio_b64"})
                    return
                result = s.judge_audio(player, body["audio_b64"], body.get("format"))
                with s.lock:
                    if s.state.pending_audio == pend:
                        s.state.pending_audio = None
                        s.engine.push_event(dict(result))
                self._json(200, {"verdict": result["verdict"], "reason": result["reason"]})
                return
            if path == "/api/stt":
                # 按住说话(PTT,房主获批 2026-07-24):玩家按住「局长」键说一句,
                # 松手后全模态口转成文字,以 say(to=局长, via=voice) 走既有 push_event
                # 入事件流——享受既有遮蔽(别人的 view 只见「跟主持说了句话」)。
                # 音频转写完即弃:不落盘、不入 episode。这是显式动作(按住才收、
                # 松手即停),不是常驻监听——感知线收束令依然有效,PTT 之外零采集。
                player = body.get("player")
                with s.lock:
                    if player not in s.state.players:
                        self._json(400, {"error": f"未知座位: {player}"})
                        return
                if not body.get("audio_b64"):
                    self._json(400, {"error": "缺 audio_b64(空音频不转写)"})
                    return
                judge = _make_audio_judge()   # 与 judge_audio 同一把 key、同一条全模态口
                if judge is None:
                    self._json(501, {"error": "语音通道未接入,打字仍可用"})
                    return
                text = s.stt_transcribe(judge, body["audio_b64"], body.get("format"))
                if text is None:
                    self._json(502, {"error": "没转写出来,再说一次(打字仍可用)"})
                    return
                with s.lock:
                    s.engine.push_event({"type": "say", "player": player,
                                         "text": text, "to": "局长", "via": "voice"})
                self._json(200, {"ok": True, "text": text})   # 回显给说话者,音频不回传
                return
            if path == "/api/photo":
                player = body.get("player")
                with s.lock:
                    pend = s.state.pending_photo
                if not pend:
                    self._json(409, {"error": "没有进行中的拍照判定"})
                    return
                if pend["player"] != player:
                    self._json(403, {"error": f"这单判定点的是 {pend['player']},不是 {player}"})
                    return
                image_b64, frames = body.get("image_b64"), body.get("frames")
                if image_b64 and frames:
                    self._json(400, {"error": "image_b64 与 frames 二选一(照片判定/视频抽帧判定不能同时交)"})
                    return
                if frames is not None:
                    if not isinstance(frames, list) or not frames or not all(frames):
                        self._json(400, {"error": "frames 需是非空 base64 字符串列表"})
                        return
                    if len(frames) > 5:
                        self._json(400, {"error": "frames 至多 5 帧"})
                        return
                elif not image_b64:
                    self._json(400, {"error": "缺 image_b64"})
                    return
                # 视觉调用在锁外(慢);回来若判定单还是同一单才结案入队
                result = s.judge_photo(player, image_b64, body.get("media_type"), frames=frames)
                with s.lock:
                    if s.state.pending_photo == pend:
                        s.state.pending_photo = None
                        s.engine.push_event(dict(result))
                self._json(200, {"verdict": result["verdict"], "reason": result["reason"]})
                return
            if path == "/api/finish":
                # 收局必须是一条不经过模型的硬路径:llm 驱动下 UI 的 tool_use 会被丢弃,
                # 走 /api/turn 收局等于再花一次钱问主持人想干嘛,而且收不掉。
                with s.lock:
                    if not s.state.finished:
                        s.state.finished = True
                        s.recent.append(s.engine.run(max_turns=s.engine.marks["turns"]))
                        persist.clear_snapshot(s)   # 收局清理快照,不留待恢复
                self._json(200, {"finished": True})
                return
            if path == "/api/turn":
                with s.lock:
                    if s.state.finished:
                        self._json(409, {"error": "局已收"})
                        return
                    red = s.run_turn(body)
                self._json(200, red)
                return
            self._json(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            # 任何异常逃逸出去,处理线程当场死掉、一个字节都不回,浏览器那头
            # 表现为「点了没反应」——真原因只在终端的 traceback 里。必须转成
            # 一条 JSON 错误送回前端,让房主在页面上就能看见。
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


def make_server(port: int, out_dir: Path, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((bind, port), Handler)
    actual = srv.server_address[1]
    # 公网部署(Caddy 同机反代 + 域名 HTTPS)时,入座链接必须是玩家手机在任何网络
    # 都打得开的公网地址,而不是本机局域网 IP。设 PUBLIC_BASE_URL=https://你的域名
    # (.env 里配一行即可,main 会先 load_env)整体覆盖 join_base;不设则行为不变,
    # 完全向后兼容局域网玩法。
    public = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public:
        Handler.hub = Hub(out_dir, join_base=public)
    else:
        # 入座链接必须用局域网地址:手机连的是 Wi-Fi,localhost 指向手机自己。
        host = lan_host() if bind not in ("127.0.0.1", "localhost") else "localhost"
        Handler.hub = Hub(out_dir, join_base=f"http://{host}:{actual}")
    return srv


def main() -> None:
    from .env import load_env
    load_env()  # 仓库根 .env 配一次永久生效(key 不进仓库);export 仍可临时覆盖
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8747)
    ap.add_argument("--out", default="outputs/episodes")
    ap.add_argument("--lan", action="store_true",
                    help="绑 0.0.0.0,让同一 Wi-Fi 下的手机能连进来(入座链接自动用局域网 IP)")
    ap.add_argument("--resume", default=None,
                    help="局中断点续局:从 sim_<stamp>.state.json 快照恢复一局继续玩(可与 --lan 组合)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    srv = make_server(args.port, out, bind="0.0.0.0" if args.lan else "127.0.0.1")
    if args.resume:
        # 服务重启后恢复整局:计时器作废清零 + resume_note 入队,episode 追加续写,
        # 对决作废;llm 主持凭 digest + 荷官回执重新入场(history 不持久化)。
        snap = persist.load_snapshot(args.resume)
        if snap.get("finished"):
            print(f"该快照已收局,不恢复:{args.resume}")
        else:
            session = persist.restore_session(snap, out, join_base=Handler.hub.join_base)
            code = session.room_code or gen_room_code(set(Handler.hub.rooms))
            session.room_code = code
            Handler.hub.rooms[code] = session
            if session.autoplay and session.driver_kind == "llm":
                session.start_autoloop(session.autoplay_interval_s)
            print(f"已从快照恢复:{args.resume}(房间码 {code},episode 追加续写 {session.episode_path})")
    base = Handler.hub.join_base
    print(f"驾驶舱 → {base}/cockpit  玩家落地页 → {base}  (机位 {MIN_PLAYERS}–{MAX_PLAYERS},Ctrl-C 退出)")
    print(f"玩家入座        → {base}/play")
    if not args.lan:
        print("  ↑ 只有本机能开。手机要入座请加 --lan(同一 Wi-Fi)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
