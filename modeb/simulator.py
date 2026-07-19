"""M2 · 多机模拟台(开发驾驶舱,非产品面)。

一页 = N 个玩家 pane(2–10 任意,机位数不改机制)+ 主持台 + 账本/episode 实时窗。
玩家事件 → engine.push_event;回合驱动:manual(主持台提交决策)或 scripted。
纯标准库(http.server),零依赖;产品端另起炉灶,本台不为浏览器妥协任何设计
(裁定纪要:浏览器损失逐件上报——本台非产品,无此问题)。

用法:python -m modeb.simulator [--port 8747]  → 浏览器开 http://localhost:8747
"""
from __future__ import annotations

import argparse
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
from .transports import make_transport

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
                 score_style: str = "自动") -> None:
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
                               score_style=score_style)
        self.driver_kind = driver_kind
        if driver_kind == "scripted":
            self.driver = ScriptedDriver()
        elif driver_kind == "llm":
            self.driver = LLMDriver(make_transport(provider, host_model),
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
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        return {
            "players": self.state.players,
            "digest": self.state.digest(self.engine.time_left_min()),
            "finished": self.state.finished,
            "marks": dict(self.engine.marks),
            "pending_events": list(self.engine.event_queue),
            "recent_turns": self.recent[-12:],
            "clamps": self.engine.tools.clamp_log[-5:],
            "episode_path": str(self.episode_path),
            "driver": self.driver_kind,
            "bots": self.bot_names,
            "turn_ready": self.engine.turn_ready(),
            # 桌友调用失败数:静默降级会让 key 错表现成「bot 好闷」,得摆到台面上
            "bot_errors": {b.name: {"count": b.errors, "last": b.last_error}
                           for b in self.bots if getattr(b, "errors", 0)},
        }


class Hub:
    def __init__(self, out_dir: Path) -> None:
        self.session: Session | None = None
        self.out_dir = out_dir

    def start(self, cfg: dict) -> dict:
        if self.session and not self.session.state.finished:
            self.session.engine._ep.close()  # 旧局落盘关账
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
        )
        return self.session.snapshot()


HTML_PATH = Path(__file__).with_name("sim_ui.html")


class Handler(BaseHTTPRequestHandler):
    hub: Hub  # 类属性注入

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, *a) -> None:  # 静默访问日志
        pass

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
                    s.recent.append(line)
                    digest = s.state.digest(s.engine.time_left_min())
                    for bot in s.bots:  # 桌友对本回合反应,入队待下回合聚合
                        for ev in bot.react(line, digest):
                            s.engine.push_event(ev)
                    if s.state.finished:
                        summary = s.engine.run(max_turns=s.engine.marks["turns"])
                        s.recent.append(summary)
                self._json(200, line)
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
