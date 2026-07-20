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
import threading
import time as _t
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .driver_llm import LLMDriver
from .driver_scripted import ScriptedDriver
from .engine import Engine
from .player_agent import LLMPlayerAgent, ScriptedPlayerAgent
from .state import GameState
from .transports import Resilient, make_transport

MIN_PLAYERS, MAX_PLAYERS = 2, 10  # 机位上限只是这一行常数,机制不感知机位数


class ManualDriver:
    """主持台驱动:/api/turn 随请求带入决策,decide 原样返回。"""

    def __init__(self) -> None:
        self.pending: dict | None = None

    def decide(self, digest: dict, events: list[dict]) -> dict:
        d = self.pending or {"text": "", "tool_use": []}
        self.pending = None
        return d


class Session:
    def __init__(self, players: list[str], minutes: int, wildness: int,
                 objects: list[str], driver_kind: str, out_dir: Path,
                 bots: dict[str, str] | None = None, provider: str = "anthropic",
                 host_model: str = "sonnet", seat_model: str = "sonnet",
                 score_style: str = "自动", host_perception: str = "转写",
                 playlist: list[str] | None = None,
                 occasion: str = "", scene_brief: str = "") -> None:
        if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS:
            raise ValueError(f"玩家数须在 {MIN_PLAYERS}–{MAX_PLAYERS}(收到 {len(players)})")
        if driver_kind == "scripted" and len(players) < 3:
            raise ValueError("scripted 演示驱动需要 ≥3 人;manual 不限")
        bots = bots or {}
        unknown = set(bots) - set(players)
        if unknown:
            raise ValueError(f"bot 座位不在玩家名单里: {sorted(unknown)}")
        self.state = GameState(players=players, wildness_cap=wildness,
                               time_budget_min=minutes, scene_objects=objects,
                               score_style=score_style, host_perception=host_perception,
                               playlist=[t.strip() for t in (playlist or []) if t.strip()],
                               occasion=occasion.strip(), scene_brief=scene_brief.strip())
        self.driver_kind = driver_kind
        if driver_kind == "scripted":
            self.driver = ScriptedDriver()
        elif driver_kind == "llm":
            # 主持包重试外套(瞬时错误等 2 秒再试一次);桌友不包,已有响亮降级
            self.driver = LLMDriver(Resilient(make_transport(provider, host_model)),
                                    players, wildness, minutes, score_style=score_style,
                                    playlist=self.state.playlist,
                                    occasion=self.state.occasion,
                                    scene_brief=self.state.scene_brief)
        else:
            self.driver = ManualDriver()
        # 视觉裁判(judge.photo):懒加载,anthropic 口(国产口视觉各家不齐,v0 不接)
        self._judge_provider = provider if provider in ("anthropic", "mock") else "anthropic"
        self._judge_model = host_model
        self._judge = None
        # 场景照更新 scene_brief 后重建主持 system 用
        self._prompt_args = dict(players=players, wildness=wildness, minutes=minutes,
                                 score_style=score_style)
        if provider == "mock":  # 测试:确定性假人
            self.bots = [ScriptedPlayerAgent(n, [[{"type": "laugh"}]] * 3) for n in bots]
        else:
            self.bots = [LLMPlayerAgent(n, persona or "普通桌友",
                                        make_transport(provider, seat_model))
                         for n, persona in bots.items()]
        self.bot_names = sorted(bots)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.episode_path = out_dir / f"sim_{stamp}.jsonl"
        self.engine = Engine(self.state, self.driver, self.episode_path)
        self.recent: list[dict] = []  # 最近回合行,供前端轮询
        self.inbox: dict[str, list[str]] = {n: [] for n in players}  # 私发收件箱(可见性引擎落地)
        self.last_timing: dict = {}  # 上一拍耗时拆帐:host_ms / bots_ms(慢要先量再修)
        self.lock = threading.Lock()
        self.join_base = ""   # 由 Hub 注入:http://<局域网IP>:<端口>

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
            vis, target = res.get("visibility"), res.get("player")
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
                self.inbox[target].append(f"🔒 {secret}")
                res[field] = "🔒私发(已投递,内容仅目标可见)"
            elif vis == "额头" and target:
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

    def judge_photo(self, player: str, image_b64: str, media_type: str | None) -> dict:
        """视觉裁判(锁外调用,慢):返回 judge_result 事件体。判不了不装懂——
        「无法判定」明说,主持按声明走 ask 共识兜底(spec §1.4)。"""
        pend = self.state.pending_photo or {}
        if self._judge is None:
            self._judge = make_transport(self._judge_provider, self._judge_model)
        sys_p = ("你是聚会游戏的视觉裁判:只按给定标准判,宽松判、气氛优先、拿不准给过。"
                 '只输出 JSON:{"verdict":"过|不过|无法判定","reason":"一句话,现场能念"}')
        msgs = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": media_type or "image/jpeg",
                                         "data": image_b64}},
            {"type": "text", "text": f"判定标准:{pend.get('prompt', '')}"}]}]
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
        # 主持沉默拍(调用失败):桌上没发生任何事,桌友无从反应,不烧调用
        bots = [] if line.get("host_silent") else self.bots
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
        return red

    def start_autoloop(self, interval_s: float = 1.0) -> None:
        """服务端自驱回合环(App 时代的回合发动机):llm 驱动下起一条守护线程,
        turn_ready 就跑拍——驾驶舱页面从此不是发动机,电脑起完服可以合盖。"""
        def _loop():
            while not self.state.finished:
                _t.sleep(interval_s)
                try:
                    with self.lock:
                        if not self.state.finished and self.engine.turn_ready():
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
                elif e.get("value"):
                    item["value"] = e["value"]
                table.append(item)
            recent.append({"turn": line.get("turn"), "host": line.get("text", ""),
                           "shown": shown, "table": table})
        return {
            "you": me,
            "round": digest.get("round"), "turn": self.engine.marks["turns"],
            "turn_ready": self.engine.turn_ready(), "focus": digest.get("focus"),
            "finished": self.state.finished,
            "time_left_min": digest.get("time_left_min"),
            "now_playing": digest.get("now_playing"),  # 音乐是全场公开的:现实里人人听得见
            # 对决状态:玩家端只看到 vs 与 drawn 布尔(枪响了没),拔枪时点不出服务端
            "duel": digest.get("duel"),
            # 拍照判定:只有被点名的人看到出题(别人只从主持嘴里听到)
            "photo_request": (self.state.pending_photo["prompt"]
                              if self.state.pending_photo
                              and self.state.pending_photo["player"] == me else None),
            "scores": digest.get("scores"), "scene_objects": digest.get("scene_objects"),
            "now_playing": digest.get("now_playing"),   # 手机上要显示正在放的歌
            "timer_running": digest.get("timer_running"),
            "inbox": self.inbox.get(me, [])[-8:],          # 只有自己的
            "open_ask": ({"prompt": ask["prompt"], "asked": ask["asked"],
                          "options": ask["options"]} if ask else None),  # 不含 answers
            "recent": recent,
        }

    def snapshot(self) -> dict:
        return {
            "players": self.state.players,
            "digest": self.state.digest(self.engine.time_left_min()),
            "finished": self.state.finished,
            "marks": dict(self.engine.marks),
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


class Hub:
    def __init__(self, out_dir: Path, join_base: str = "") -> None:
        self.session: Session | None = None
        self.out_dir = out_dir
        self.join_base = join_base

    def start(self, cfg: dict) -> dict:
        if self.session and not self.session.state.finished:
            # 关账要拿旧局的锁:自动回合可能还有飞行中的 turn 握着这个 session,
            # 抢在它写完前关文件,它就会撞 "I/O operation on closed file"。
            old = self.session
            with old.lock:
                old.state.finished = True   # 关门后到的回合走 409,不再往这局写
                old.engine._ep.close()
        players = [p.strip() for p in cfg.get("players", []) if p.strip()]
        self.session = Session(
            players=players,
            minutes=int(cfg.get("minutes", 30)),
            wildness=int(cfg.get("wildness", 6)),
            objects=[o.strip() for o in cfg.get("objects", []) if o.strip()],
            driver_kind=cfg.get("driver", "manual"),
            out_dir=self.out_dir,
            bots=cfg.get("bots") or {},
            provider=cfg.get("provider", "anthropic"),
            host_model=cfg.get("host_model", "sonnet"),
            seat_model=cfg.get("seat_model", "sonnet"),
            score_style=cfg.get("score_style", "自动"),
            host_perception=cfg.get("host_perception", "转写"),
            playlist=cfg.get("playlist") or [],
            occasion=cfg.get("occasion", ""), scene_brief=cfg.get("scene_brief", ""),
        )
        self.session.join_base = self.join_base
        if cfg.get("autoplay") and cfg.get("driver") == "llm":
            # 服务端自驱:回合发动机进服务器,驾驶舱页面可关、电脑可合盖
            self.session.start_autoloop(float(cfg.get("autoplay_interval_s", 1.0)))
        return self.session.snapshot()


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

    def _seat_param(self) -> str:
        """取 ?player= 座位名。http.server 按 latin-1 解请求行,裸中文会变成
        å°å——转回 utf-8 再认一次,免得中文座位名默认用不了。"""
        from urllib.parse import parse_qs, urlparse
        player = (parse_qs(urlparse(self.path).query).get("player") or [""])[0]
        s0 = self.hub.session
        if s0 and player not in s0.inbox:
            try:
                player = player.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        return player

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html") or self.path.startswith("/play"):
            # /play[?player=X] = 玩家页(手机);/ = 驾驶舱(房主)。两页两套视角,
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
            s0 = self.hub.session
            if s0 is None:
                self._json(409, {"error": "先开局"}); return
            player = self._seat_param()
            if player not in s0.inbox:
                self._json(400, {"error": f"未知座位: {player}"}); return
            if self.path.startswith("/api/view"):
                # 玩家视图:一台手机该看见的东西。带进度(否则玩家还得回驾驶舱),
                # 不含 tool_use / results / inbox_counts / 别人的收件箱。
                self._json(200, s0.player_view(player))
            else:
                self._json(200, {"player": player, "inbox": s0.inbox[player][-8:]})
            return
        if self.path == "/api/state":
            s = self.hub.session
            self._json(200, s.snapshot() if s else {"no_session": True,
                       "limits": {"min_players": MIN_PLAYERS, "max_players": MAX_PLAYERS}})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/api/start":
                self._json(200, self.hub.start(self._body()))
                return
            s = self.hub.session
            if s is None:
                self._json(409, {"error": "先 /api/start 开局"})
                return
            if self.path == "/api/event":
                ev = self._body()
                with s.lock:
                    s.engine.push_event(ev)
                self._json(200, {"queued": ev})
                return
            if self.path == "/api/scene":
                body = self._body()
                if not body.get("image_b64"):
                    self._json(400, {"error": "缺 image_b64"})
                    return
                out = s.scene_photo(body["image_b64"], body.get("media_type"))
                self._json(200 if "error" not in out else 502, out)
                return
            if self.path == "/api/photo":
                body = self._body()
                player = body.get("player")
                with s.lock:
                    pend = s.state.pending_photo
                if not pend:
                    self._json(409, {"error": "没有进行中的拍照判定"})
                    return
                if pend["player"] != player:
                    self._json(403, {"error": f"这单判定点的是 {pend['player']},不是 {player}"})
                    return
                if not body.get("image_b64"):
                    self._json(400, {"error": "缺 image_b64"})
                    return
                # 视觉调用在锁外(慢);回来若判定单还是同一单才结案入队
                result = s.judge_photo(player, body["image_b64"], body.get("media_type"))
                with s.lock:
                    if s.state.pending_photo == pend:
                        s.state.pending_photo = None
                        s.engine.push_event(dict(result))
                self._json(200, {"verdict": result["verdict"], "reason": result["reason"]})
                return
            if self.path == "/api/finish":
                # 收局必须是一条不经过模型的硬路径:llm 驱动下 UI 的 tool_use 会被丢弃,
                # 走 /api/turn 收局等于再花一次钱问主持人想干嘛,而且收不掉。
                with s.lock:
                    if not s.state.finished:
                        s.state.finished = True
                        s.recent.append(s.engine.run(max_turns=s.engine.marks["turns"]))
                self._json(200, {"finished": True})
                return
            if self.path == "/api/turn":
                body = self._body()
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
    # 入座链接必须用局域网地址:手机连的是 Wi-Fi,localhost 指向手机自己。
    host = lan_host() if bind not in ("127.0.0.1", "localhost") else "localhost"
    Handler.hub = Hub(out_dir, join_base=f"http://{host}:{actual}")
    return srv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8747)
    ap.add_argument("--out", default="outputs/episodes")
    ap.add_argument("--lan", action="store_true",
                    help="绑 0.0.0.0,让同一 Wi-Fi 下的手机能连进来(入座链接自动用局域网 IP)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    srv = make_server(args.port, out, bind="0.0.0.0" if args.lan else "127.0.0.1")
    base = Handler.hub.join_base
    print(f"模拟台(驾驶舱) → {base}  (机位 {MIN_PLAYERS}–{MAX_PLAYERS},Ctrl-C 退出)")
    print(f"玩家入座        → {base}/play")
    if not args.lan:
        print("  ↑ 只有本机能开。手机要入座请加 --lan(同一 Wi-Fi)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
