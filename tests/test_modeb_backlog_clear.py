"""后置清单清仓(房主令:没必要后置):轮流问询/语境收窄/私件挂账/场景侦察。"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.engine import Engine  # noqa: E402
from modeb.simulator import make_server  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


class _IdleDriver:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path, players=("甲", "乙", "丙")):
    state = GameState(players=list(players), wildness_cap=6, time_budget_min=30)
    return Engine(state, _IdleDriver(), tmp_path / "ep.jsonl")


# —— 轮流问询:每人独立应答槽,谁也挤不掉谁 ——

def test_round_robin_ask_full_cycle(tmp_path):
    eng = _engine(tmp_path)
    r = eng.tools.execute({"name": "ask", "input": {
        "prompt": "轮流说一个形容词", "mode": "轮流", "window": 5}})
    assert r["ok"] and r["result"]["order"] == ["甲", "乙", "丙"]
    assert eng.state.open_ask["asked"] == "甲"

    eng.push_event({"type": "say", "player": "甲", "text": "圆的", "to": "局长"})
    assert eng.state.open_ask["asked"] == "乙", "答完立即轮下一位,不烧完窗口"
    eng.push_event({"type": "say", "player": "丙", "text": "抢!", "to": "局长"})
    assert "丙" not in eng.state.open_ask["answers"], "没轮到丙,抢不进"

    eng.state.open_ask["deadline"] = time.time() - 0.1  # 乙超时
    assert eng._close_ask() is None, "中途超时只轮转,不出结果"
    assert eng.state.open_ask["asked"] == "丙"

    eng.push_event({"type": "say", "player": "丙", "text": "软的", "to": "局长"})
    eng.state.open_ask["deadline"] = time.time() - 0.1
    result = eng._close_ask()
    assert result and result["answers"] == {"甲": "圆的", "丙": "软的"}
    assert result["silent"] == ["乙"], "轮到没接的人点名可见,且不算故意沉默"


# —— draw_atom 语境软收窄 ——

def test_draw_atom_context_narrows():
    ex = ToolExecutor(GameState(players=["甲", "乙", "丙", "丁"],
                                wildness_cap=8, time_budget_min=30))
    hits = set()
    for i in range(6):
        ex2 = ToolExecutor(GameState(players=["甲", "乙", "丙", "丁"],
                                     wildness_cap=8, time_budget_min=30), rng_seed=i)
        r = ex2.execute({"name": "draw_atom", "input": {
            "atom_type": "完整玩法", "context": "传话游戏 依次传话 悄悄话"}})
        assert r["ok"]
        hits.add(r["result"]["atom"]["name"])
    assert any(("传" in n) or ("话" in n) for n in hits), \
        f"语境收窄后候选应明显偏向传话类,实际: {hits}"


# —— 私件挂账 ——

def test_private_out_ledger():
    ex = ToolExecutor(GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30))
    ex.execute({"name": "show", "input": {"content": "词:西瓜", "visibility": "额头", "player": "甲"}})
    ex.execute({"name": "show", "input": {"content": "任务", "visibility": "自己看",
                                          "players": ["乙", "丙"]}})
    out = ex.state.digest(30.0)["private_out"]
    assert {"holder": "甲", "kind": "额头"} in out
    assert {"holder": "乙", "kind": "自己看"} in out and {"holder": "丙", "kind": "自己看"} in out
    assert all("content" not in x and "西瓜" not in json.dumps(x, ensure_ascii=False)
               for x in out), "挂账只记去向不记内容(digest 是半公开面)"


# —— 场景侦察 /api/scene ——

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


class _FakeScout:
    def complete(self, system, messages):
        return '{"objects": ["投影仪", "抱枕", "冰桶"], "brief": "客厅轰趴,有投影和落地窗"}'


def test_scene_photo_updates_state(server, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _FakeScout())
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": ["瓶子"], "driver": "manual"})
    res, code = call(server, "/api/scene", {"image_b64": "aGk=", "media_type": "image/jpeg"})
    assert code == 200 and "投影仪" in res["objects"] and "轰趴" in res["brief"]
    snap, _ = call(server, "/api/state")
    assert "投影仪" in snap["digest"]["scene_objects"], "侦察结果并入实物清单"
    assert "瓶子" in snap["digest"]["scene_objects"], "手填的不丢,合并不覆盖"
