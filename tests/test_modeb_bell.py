"""系统级炸铃(喊停类玩法:传花停/木头人/数到停):timer 带 fx 落铃。

判定时刻是那声"停!"——局长没嗓子、文字停没人看见(玩家抬头玩)。设计(房主裁定):
像快枪手 draw_at 一样,时刻由系统精确执行,LLM 不在回路;铃是公开广播,人人要响,
全桌手机用 server_now 算钟差、本地精确定时齐响。本测守四条边:
落铃正确 / 人人同铃 / 无 fx 不落铃 / 新铃覆盖旧铃 / 过期即撤 / timer 正常到期不变。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.simulator import Session  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _IdleDriver:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30)
    return Engine(state, _IdleDriver(), tmp_path / "ep.jsonl")


def _session(tmp_path):
    return Session(players=["甲", "乙", "丙"], minutes=30, wildness=6, objects=[],
                   driver_kind="manual", out_dir=tmp_path)


def test_timer_fx_sets_pending_bell(tmp_path):
    eng = _engine(tmp_path)
    before = time.time()
    r = eng.tools.execute({"name": "timer", "input": {"seconds": 5, "fx": "停"}})
    assert r["ok"] and r["result"]["bell"] == "停"
    bell = eng.state.pending_bell
    assert bell is not None and bell["fx"] == "停"
    assert before + 5 - 0.5 <= bell["at"] <= time.time() + 5 + 0.5, "铃 at = timer 到期 epoch"
    # timer 正常入 timers,到期逻辑不受炸铃影响(同一时刻)
    assert eng.state.timers and abs(eng.state.timers[-1] - bell["at"]) < 1e-6


def test_timer_without_fx_no_bell(tmp_path):
    eng = _engine(tmp_path)
    r = eng.tools.execute({"name": "timer", "input": {"seconds": 5}})
    assert r["ok"] and "bell" not in r["result"], "不带 fx 的 timer 不产生铃"
    assert eng.state.pending_bell is None


def test_new_bell_overrides_old(tmp_path):
    eng = _engine(tmp_path)
    eng.tools.execute({"name": "timer", "input": {"seconds": 5, "fx": "停"}})
    eng.tools.execute({"name": "timer", "input": {"seconds": 8, "fx": "开始"}})
    # 同一时刻只挂一个铃:新铃覆盖旧铃
    assert eng.state.pending_bell["fx"] == "开始"


def test_all_players_view_same_bell(tmp_path):
    sess = _session(tmp_path)
    sess.engine.tools.execute({"name": "timer", "input": {"seconds": 5, "fx": "停"}})
    views = {p: sess.player_view(p) for p in ["甲", "乙", "丙"]}
    # 铃是公开广播:每台手机的 view 都带同一个 bell(at 一致)
    ats = {v["bell"]["at"] for v in views.values()}
    assert len(ats) == 1, "人人看到同一个铃(at 完全一致)"
    for v in views.values():
        assert v["bell"]["fx"] == "停"
        # server_now 始终下发,客户端据此算钟差;剩余时长约等于设定的 5 秒
        assert "server_now" in v
        assert 4.0 <= v["bell"]["at"] - v["server_now"] <= 5.5


def test_view_no_bell_without_fx(tmp_path):
    sess = _session(tmp_path)
    sess.engine.tools.execute({"name": "timer", "input": {"seconds": 5}})
    v = sess.player_view("甲")
    assert v["bell"] is None, "无 fx 的 timer 不给 view 落铃"
    assert "server_now" in v, "server_now 始终下发(客户端恒需算钟差)"


def test_expired_bell_dropped_from_view(tmp_path):
    sess = _session(tmp_path)
    # 过期 4 秒(>3s):不再下发,响过就撤
    sess.state.pending_bell = {"at": time.time() - 4.0, "fx": "停"}
    assert sess.player_view("甲")["bell"] is None
    # 刚过期 1 秒:仍在 3 秒宽限窗口内,照发(赶上响那一下)
    sess.state.pending_bell = {"at": time.time() - 1.0, "fx": "停"}
    assert sess.player_view("甲")["bell"] is not None


def test_timer_expired_still_wakes_host(tmp_path):
    eng = _engine(tmp_path)
    eng.tools.execute({"name": "timer", "input": {"seconds": 1, "fx": "停"}})
    eng.state.timers = [time.time() - 0.01]  # 强制到点
    assert eng.turn_ready(), "带铃的 timer 到点照常叫醒主持"
    line = eng.turn()
    assert any(e.get("type") == "timer_expired" for e in line["events_in"]), \
        "timer_expired 仍照发,炸铃不改 timer 正常到期逻辑"
