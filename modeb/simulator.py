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
import traceback
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
                 score_style: str = "自动", host_perception: str = "转写") -> None:
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
                               score_style=score_style, host_perception=host_perception)
        self.driver_kind = driver_kind
        if driver_kind == "scripted":
            self.driver = ScriptedDriver()
        elif driver_kind == "llm":
            # 主持包重试外套(瞬时错误等 2 秒再试一次);桌友不包,已有响亮降级
            self.driver = LLMDriver(Resilient(make_transport(provider, host_model)),
                                    players, wildness, minutes, score_style=score_style)
        else:
            self.driver = ManualDriver()
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
        self.lock = threading.Lock()

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
            if vis == "自己看" and target:
                self.inbox[target].append(f"🔒 {secret}")
                res[field] = "🔒私发(已投递,内容仅目标可见)"
                # 收件人不遮:/api/state 是驾驶舱(非产品面),房主调试时本来就该
                # 看见谁拿到了什么。实测里"元数据泄漏"是 agent 轮询调试口造成的,
                # 真实玩家看不到这个端点——遮了反而把调试工具弄瞎。
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
            "scores": digest.get("scores"), "scene_objects": digest.get("scene_objects"),
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
            "host_perception": self.state.host_perception,
            "turn_ready": self.engine.turn_ready(),
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
    def __init__(self, out_dir: Path) -> None:
        self.session: Session | None = None
        self.out_dir = out_dir

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
        )
        return self.session.snapshot()


HTML_PATH = Path(__file__).with_name("sim_ui.html")


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
        if self.path in ("/", "/index.html"):
            body = HTML_PATH.read_bytes()
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
                    if isinstance(s.driver, ManualDriver):
                        s.driver.pending = {"text": body.get("text", ""),
                                            "tool_use": body.get("tool_use", [])}
                    line = s.engine.turn()
                    # 出服务端的一律是遮蔽版:回合响应、轮询面、桌友输入全走 red。
                    # 原文只活在 episode 文件(审计)和目标收件箱里——防偷看在
                    # 服务端成立,不靠任何客户端自觉。
                    red = s.route_private(line)
                    s.recent.append(red)
                    digest = s.state.digest(s.engine.time_left_min())
                    # 主持沉默拍(调用失败):桌上没发生任何事,桌友无从反应,不烧调用
                    bots = [] if line.get("host_silent") else s.bots
                    for bot in bots:  # 桌友对本回合反应,入队待下回合聚合;私件只随本人
                        try:
                            evs = bot.react(red, digest, inbox=s.inbox.get(bot.name, [])[-3:])
                        except TypeError:
                            evs = bot.react(red, digest)
                        for ev in evs:
                            s.engine.push_event(ev)
                    if s.state.finished:
                        summary = s.engine.run(max_turns=s.engine.marks["turns"])
                        s.recent.append(summary)
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


def make_server(port: int, out_dir: Path) -> ThreadingHTTPServer:
    Handler.hub = Hub(out_dir)
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8747)
    ap.add_argument("--out", default="outputs/episodes")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    srv = make_server(args.port, out)
    print(f"模拟台 → http://localhost:{args.port}  (机位 {MIN_PLAYERS}–{MAX_PLAYERS},Ctrl-C 退出)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
