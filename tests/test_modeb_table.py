"""M2 三/四单验收:传输层 payload 形状、桌友 agent 解析纪律、整桌编排、模拟台 bot 座位。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.driver_llm import MockTransport  # noqa: E402
from modeb.driver_scripted import ScriptedDriver  # noqa: E402
from modeb.engine import Engine  # noqa: E402
from modeb.player_agent import LLMPlayerAgent, ScriptedPlayerAgent, parse_player_events  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.table import TableRunner  # noqa: E402
from modeb.simulator import make_server  # noqa: E402


# —— 传输层:不碰网络,截获 payload 验形状 ——
def test_anthropic_payload_shape(monkeypatch):
    from modeb import transports
    captured = {}

    def fake_post(url, headers, payload, timeout=60):
        captured.update(url=url, headers=headers, payload=payload)
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(transports, "_post_json", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    t = transports.AnthropicTransport("haiku")
    out = t.complete("SYS", [{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "k-test"
    assert captured["payload"]["system"] == "SYS"
    assert captured["payload"]["model"].startswith("claude-haiku")


def test_openai_compat_payload_shape(monkeypatch):
    from modeb import transports
    captured = {}

    def fake_post(url, headers, payload, timeout=60):
        captured.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(transports, "_post_json", fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk-test")
    t = transports.OpenAICompatTransport()
    assert t.complete("SYS", [{"role": "user", "content": "hi"}]) == "ok"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["payload"]["model"] == "deepseek-chat"


def test_transport_requires_key(monkeypatch):
    from modeb.transports import AnthropicTransport
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport().complete("s", [])


# —— 桌友 agent 纪律 ——
def test_player_events_identity_pinned():
    raw = json.dumps({"events": [
        {"type": "vote", "value": "赞成", "player": "假冒者"},
        {"type": "laugh"}, {"type": "tap"}]}, ensure_ascii=False)
    evs = parse_player_events(raw, "小静")
    assert len(evs) == 2, "事件截断 ≤2"
    assert all(e["player"] == "小静" for e in evs), "座位身份系统钉死,不得冒名"


def test_player_agent_bad_output_is_silence():
    agent = LLMPlayerAgent("大鹏", "吐槽王", MockTransport(["不是JSON的废话"]))
    assert agent.react({"text": "x", "results": []}, {"round": 1, "scores": {}}) == []


def test_player_agent_transport_error_no_crash():
    class Boom:
        def complete(self, *a):
            raise RuntimeError("网络炸了")
    agent = LLMPlayerAgent("阿伟", "显眼包", Boom())
    assert agent.react({"text": "x", "results": []}, {"round": 1, "scores": {}}) == []


def test_disallowed_event_type_filtered():
    raw = json.dumps({"events": [{"type": "state.add_score", "delta": 3}]})
    assert parse_player_events(raw, "老宋") == [], "桌友只许出玩家事件,不许摸工具"


# —— 整桌编排:主持脚本 + 桌友脚本,事件在下一回合聚合 ——
def test_table_runner_bot_events_next_turn(tmp_path):
    state = GameState(players=["疯子明", "小静", "大鹏"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子", "冰块", "纸巾", "手机", "杯子", "打火机"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=7)
    bots = [ScriptedPlayerAgent("小静", [[{"type": "laugh"}]] * 12)]
    runner = TableRunner(eng, bots)
    runner.run_turn()
    line2 = runner.run_turn()
    assert {"type": "laugh", "player": "小静"} in line2["events_in"], "bot 事件须在下一回合聚合"
    summary = runner.run_to_finish()
    assert state.finished and summary["laugh_events"] >= 9


# —— 模拟台 bot 座位(provider=mock 确定性假人) ——
@pytest.fixture()
def server(tmp_path):
    srv = make_server(0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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


def test_simulator_bot_seats(server):
    snap, code = call(server, "/api/start", {
        "players": ["真人", "波特", "琳琳"], "minutes": 30, "wildness": 6,
        "objects": ["瓶子"], "driver": "manual", "provider": "mock",
        "bots": {"波特": "显眼包", "琳琳": "气氛组"}})
    assert code == 200 and snap["bots"] == ["波特", "琳琳"]
    call(server, "/api/turn", {"text": "开场", "tool_use": [{"name": "state.next_round", "input": {}}]})
    snap, _ = call(server, "/api/state")
    assert {"type": "laugh", "player": "波特"} in snap["pending_events"]
    assert {"type": "laugh", "player": "琳琳"} in snap["pending_events"]


def test_simulator_rejects_unknown_bot_seat(server):
    bad, code = call(server, "/api/start", {
        "players": ["甲", "乙"], "minutes": 30, "wildness": 6, "objects": [],
        "driver": "manual", "provider": "mock", "bots": {"丙": ""}})
    assert code == 400 and "丙" in bad["error"]
