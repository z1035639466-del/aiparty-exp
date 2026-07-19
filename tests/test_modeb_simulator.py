"""M2 模拟台验收:任意机位(2–10)、事件注入、manual 回合、episode 落盘。零浏览器纯 HTTP。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.simulator import MAX_PLAYERS, make_server  # noqa: E402


@pytest.fixture()
def server(tmp_path):
    srv = make_server(0, tmp_path)  # 端口 0 随机可用端口
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base, path, body=None):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _start(base, n_players, driver="manual"):
    players = [f"玩家{i}" for i in range(1, n_players + 1)]
    return call(base, "/api/start", {"players": players, "minutes": 30, "wildness": 6,
                                     "objects": ["瓶子", "冰块", "手机"], "driver": driver})


def test_arbitrary_player_count(server):
    for n in (2, 5, 10):
        snap, code = _start(server, n)
        assert code == 200 and len(snap["players"]) == n
    bad, code = _start(server, 11)
    assert code == 400 and str(MAX_PLAYERS) in bad["error"]


def test_manual_turn_and_events(server):
    _start(server, 5)
    call(server, "/api/event", {"type": "laugh", "player": "玩家3"})
    call(server, "/api/event", {"type": "vote", "player": "玩家2", "value": "赞成"})
    line, code = call(server, "/api/turn", {
        "text": "开局!", "tool_use": [{"name": "state.next_round", "input": {}}]})
    assert code == 200 and line["events_in"][0]["type"] == "laugh"
    snap, _ = call(server, "/api/state")
    assert snap["digest"]["round"] == 1 and snap["marks"]["laugh_events"] == 1


def test_clamp_surface_in_state(server):
    _start(server, 4)
    call(server, "/api/turn", {"text": "", "tool_use": [
        {"name": "state.add_score", "input": {"player": "玩家1", "delta": 9}}]})
    snap, _ = call(server, "/api/state")
    assert snap["clamps"] and "越界" in snap["clamps"][-1]["clamped"]
    assert snap["digest"]["scores"]["玩家1"] == 0


def test_finish_writes_summary(server, tmp_path):
    _start(server, 3)
    call(server, "/api/turn", {"text": "收!", "tool_use": [{"name": "state.finish", "input": {}}]})
    snap, _ = call(server, "/api/state")
    assert snap["finished"] and snap["recent_turns"][-1]["episode_summary"]
    lines = Path(snap["episode_path"]).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["episode_summary"] is True
    _, code = call(server, "/api/turn", {"text": "再来?", "tool_use": []})
    assert code == 409, "局已收须拒绝再执行回合"


def test_scripted_driver_full_game_via_http(server):
    _start(server, 3, driver="scripted")
    for _ in range(12):
        _, code = call(server, "/api/turn", {})
        if code == 409:
            break
    snap, _ = call(server, "/api/state")
    assert snap["finished"], "scripted 驱动应在模拟台上跑完整局"


class _CountingTransport:
    """记账用假传输:每次模型调用 +1,返回一个合法的空决策。

    用来证明某条路径「不烧 API」——断言必须配对照组,
    否则一个从不调用任何东西的测试也能通过,等于没测。
    """

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, messages):
        self.calls += 1
        return json.dumps({"text": "继续。", "tool_use": []}, ensure_ascii=False)


def _start_llm_table(base, monkeypatch):
    """llm 主持 + 两个 bot 座位,全部挂同一个记账传输。"""
    tr = _CountingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(base, "/api/start", {"players": ["我", "阿伟", "琳琳"],
                              "bots": {"阿伟": "显眼包", "琳琳": "气氛组组长"},
                              "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                              "driver": "llm", "provider": "deepseek"})
    return tr


def test_finish_costs_no_api_call(server, monkeypatch):
    """收局必须是零模型调用:llm 驱动下 UI 的 tool_use 会被丢弃,
    走 /api/turn 收局既收不掉、还白烧一次钱。"""
    tr = _start_llm_table(server, monkeypatch)
    assert tr.calls == 0, "开局本身不该调用模型"

    body, status = call(server, "/api/finish", {})
    assert status == 200 and body["finished"] is True
    assert tr.calls == 0, f"收局烧了 {tr.calls} 次模型调用,应为 0"

    snap, _ = call(server, "/api/state")
    assert snap["finished"] is True, "收局后 finished 必须为真"

    call(server, "/api/finish", {})          # 重复收局
    assert tr.calls == 0, "重复收局仍不该调用模型"


def test_turn_does_cost_api_calls(server, monkeypatch):
    """对照组:证明上面那个计数器真的会动——否则零调用的断言毫无意义。"""
    tr = _start_llm_table(server, monkeypatch)
    call(server, "/api/turn", {})
    assert tr.calls >= 3, f"一回合应有 1 次主持 + 2 次座位调用,实际 {tr.calls}"


def test_turn_exception_returns_json_500_and_server_survives(server, monkeypatch):
    """异常逃逸会让处理线程当场死掉、一个字节都不回,浏览器那头表现为
    「点了没反应」——真原因只在终端。必须转成 JSON 错误送回前端。"""
    class _Exploding:
        def complete(self, system, messages):
            raise RuntimeError("模拟 429 限流")

    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _Exploding())
    call(server, "/api/start", {"players": ["我", "阿伟"], "bots": {"阿伟": "显眼包"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "llm", "provider": "deepseek"})

    body, status = call(server, "/api/turn", {})
    assert status == 500, f"异常应返回 500,实际 {status}"
    assert "429" in body["error"], f"错误正文要带原因,实际 {body}"

    snap, code = call(server, "/api/state")     # 服务必须还活着
    assert code == 200 and snap["finished"] is False
