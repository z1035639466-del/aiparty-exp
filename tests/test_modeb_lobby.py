"""大厅态开房改革(房主裁定 2026-07-24):房主什么都不填直接开房拿码,朋友自己进来
打自己的名字,人齐了锁定开打。

现状是房主替全桌预填座位名(/api/start 必带 players),真实聚会没人愿意当打字秘书。
大厅态让 /api/start 允许 players 缺省 → 立刻返回房间码、**不建引擎不烧 LLM**;朋友经
/api/join 自己报名;房主经 /api/lock 用最终名单构建 Session 开打、座位封闭。

风格随 test_modeb_start_key:直接拿 Hub 打,不起 HTTP。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.simulator import MAX_PLAYERS, Hub  # noqa: E402


def _hub(tmp_path) -> Hub:
    return Hub(tmp_path)


def _open(hub, **cfg):
    """开一个大厅房(players 缺省),默认 mock provider 免得锁定后碰真 LLM。"""
    cfg.setdefault("provider", "mock")
    cfg.setdefault("driver", "manual")
    return hub.start(cfg)


# —— 1. 空 players 开房拿码:立刻返回房间码,不建引擎不烧钱 —— #

def test_empty_players_opens_lobby_no_engine(tmp_path):
    hub = _hub(tmp_path)
    out = _open(hub)
    code = out["room_code"]
    assert code and out["lobby"] is True and out["started"] is False
    assert out["host_token"], "开房要返回 host_token 给房主认人"
    assert out["roster"] == []
    # 大厅房进 lobbies、不进 rooms:引擎尚未建
    assert code in hub.lobbies and code not in hub.rooms
    assert "digest" not in out, "大厅态不该有引擎快照字段"
    # 不建引擎的硬证据:Session 构造即写 episode 文件,大厅态一个都不该有
    assert not list(tmp_path.glob("sim_*.jsonl")), "开房这一下不该建引擎/落 episode"


def test_missing_players_key_also_opens_lobby(tmp_path):
    """players 整个缺省(不是空列表)同样进大厅态。"""
    hub = _hub(tmp_path)
    out = hub.start({"driver": "manual", "provider": "mock"})
    assert out.get("lobby") is True and out["room_code"] in hub.lobbies


# —— 2. join 报名 / 去空格 / 重名驳回 / 满员驳回 —— #

def test_join_seats_and_strips_and_dedupes(tmp_path):
    hub = _hub(tmp_path)
    code = _open(hub)["room_code"]
    out, st = hub.join(code, "  疯子明  ", "dev-a")   # 去空格
    assert st == 200 and out["you"] == "疯子明" and out["roster"] == ["疯子明"]
    out, st = hub.join(code, "小静", "dev-b")
    assert st == 200 and out["roster"] == ["疯子明", "小静"]
    # 重名(不同设备)驳回,提示换一个
    out, st = hub.join(code, "疯子明", "dev-c")
    assert st == 409 and "换一个" in out["error"]
    # 同名 + 同设备 = 断线重连,放行且不重复加
    out, st = hub.join(code, "疯子明", "dev-a")
    assert st == 200 and hub.lobbies[code].names().count("疯子明") == 1
    # 空名字驳回
    _, st = hub.join(code, "   ", "dev-x")
    assert st == 400


def test_join_full_rejects(tmp_path):
    hub = _hub(tmp_path)
    code = _open(hub)["room_code"]
    for i in range(MAX_PLAYERS):
        _, st = hub.join(code, f"P{i}", f"dev{i}")
        assert st == 200
    out, st = hub.join(code, "迟到的", "dev-late")
    assert st == 409 and "满" in out["error"]
    assert len(hub.lobbies[code].roster) == MAX_PLAYERS


# —— 3. lock 后:引擎起、名单正确、join 被封闭 —— #

def test_lock_builds_engine_and_seals(tmp_path):
    hub = _hub(tmp_path)
    op = _open(hub)
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "甲", "dev-jia")
    hub.join(code, "乙", "dev-yi")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200 and snap["players"] == ["甲", "乙"]
    # 大厅转正:进 rooms、出 lobbies,引擎/episode 就位
    assert code in hub.rooms and code not in hub.lobbies
    assert list(tmp_path.glob("sim_*.jsonl")), "锁定后才建引擎、落 episode"
    # 花名册设备锚点已绑(走既有 bind_device)
    assert hub.rooms[code].device_map == {"甲": "dev-jia", "乙": "dev-yi"}
    # 座位封闭:新名字 join 驳回「本局已开打」
    out, st = hub.join(code, "丙", "dev-bing")
    assert st == 409 and "已开打" in out["error"]
    # 同名 + 同设备重连放行
    out, st = hub.join(code, "甲", "dev-jia")
    assert st == 200 and out["started"] is True and out["room_code"] == code
    # 同名换设备:座位已被占,驳回
    _, st = hub.join(code, "甲", "dev-other")
    assert st == 409


def test_lock_merges_bots_from_open_cfg(tmp_path):
    """bots 配置留在开房参数里,锁定时并入最终名单(补成 AI 座位)。"""
    hub = _hub(tmp_path)
    op = _open(hub, bots={"阿伟": "显眼包"})
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "我", "dev-me")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200 and set(snap["players"]) == {"我", "阿伟"}
    assert snap["bots"] == ["阿伟"]


def test_lock_too_few_people_rejected_and_retryable(tmp_path):
    hub = _hub(tmp_path)
    op = _open(hub)
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "独行侠", "dev-solo")
    out, st = hub.lock_room(code, token, None)
    assert st == 400 and code in hub.lobbies, "人不够:驳回且大厅仍在,可补人重锁"
    hub.join(code, "第二个", "dev-2")
    snap, st = hub.lock_room(code, token, None)   # 补齐后重锁成功(started 回滚过)
    assert st == 200 and code in hub.rooms


# —— 4. 老式带 players 的 start 行为完全不变 —— #

def test_classic_start_with_players_unchanged(tmp_path):
    hub = _hub(tmp_path)
    snap = hub.start({"players": ["甲", "乙"], "driver": "manual", "provider": "mock"})
    assert snap.get("room_code") and snap["players"] == ["甲", "乙"]
    assert "lobby" not in snap, "带 players 走老路,不是大厅态"
    assert snap["room_code"] in hub.rooms and not hub.lobbies


# —— 5. 开局口令闸对大厅开房同样生效 —— #

def test_start_key_gate_applies_to_lobby(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAKZOK_START_KEY", "开门")
    hub = _hub(tmp_path)
    with pytest.raises(ValueError):
        hub.start({"driver": "manual", "provider": "mock"})           # 缺口令
    with pytest.raises(ValueError):
        hub.start({"driver": "manual", "provider": "mock", "key": "错的"})
    out = hub.start({"driver": "manual", "provider": "mock", "key": "开门"})
    assert out.get("lobby") is True and out["room_code"] in hub.lobbies
    # 口令不进大厅留痕面
    assert "开门" not in str(out)
    assert "开门" not in str(hub.lobbies[out["room_code"]].cfg)


# —— 6. 非房主 lock 驳回;房主可凭 host_token 或 device_id 认人 —— #

def test_non_host_lock_rejected(tmp_path):
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "甲", "dev-jia")
    hub.join(code, "乙", "dev-yi")
    # 错 token + 非开房设备 → 403,大厅未动
    out, st = hub.lock_room(code, "假token", "dev-jia")
    assert st == 403 and code in hub.lobbies
    # 凭开房 device_id 认人(无 token)→ 放行
    snap, st = hub.lock_room(code, None, "host-dev")
    assert st == 200 and code in hub.rooms


def test_host_token_authorizes_lock(tmp_path):
    hub = _hub(tmp_path)
    op = _open(hub)
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "甲", "d1")
    hub.join(code, "乙", "d2")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200


# —— 大厅轮询口:roster 可见,锁定后回 started —— #

def test_lobby_state_roster_then_started(tmp_path):
    hub = _hub(tmp_path)
    op = _open(hub)
    code, token = op["room_code"], op["host_token"]
    hub.join(code, "甲", "d1")
    st_out, code_http = hub.lobby_state(code)
    assert code_http == 200 and st_out["roster"] == ["甲"] and st_out["started"] is False
    hub.join(code, "乙", "d2")
    hub.lock_room(code, token, None)
    st_out, _ = hub.lobby_state(code)
    assert st_out["started"] is True and st_out["players"] == ["甲", "乙"]


# —— HTTP 路由贯通:/api/start(空) → /api/join → /api/lobby → /api/lock 全走一遍 —— #

def test_lobby_full_flow_over_http(tmp_path):
    import json
    import threading
    import urllib.error
    import urllib.request

    from modeb.simulator import make_server

    srv = make_server(0, tmp_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(
            base + path, method="POST" if body is not None else "GET",
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read()), r.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    try:
        # 空手开房拿码
        op, st = call("/api/start", {"provider": "mock", "driver": "manual",
                                     "device_id": "host-dev"})
        assert st == 200 and op["lobby"] is True
        code, token = op["room_code"], op["host_token"]
        # 朋友自己 join
        _, st = call("/api/join", {"room": code, "name": "甲", "device_id": "d1"})
        assert st == 200
        out, st = call("/api/join", {"room": code, "name": "甲", "device_id": "d2"})
        assert st == 409 and "换一个" in out["error"]     # 重名驳回
        call("/api/join", {"room": code, "name": "乙", "device_id": "d2"})
        # 大厅轮询看名单
        lob, st = call("/api/lobby?room=" + code)
        assert st == 200 and lob["roster"] == ["甲", "乙"]
        # 非房主 lock 驳回
        out, st = call("/api/lock", {"room": code, "host_token": "假", "device_id": "d1"})
        assert st == 403
        # 房主 lock 开打
        snap, st = call("/api/lock", {"room": code, "host_token": token})
        assert st == 200 and snap["players"] == ["甲", "乙"]
        # 开打后大厅轮询回 started(App 据此切游戏页)
        lob, st = call("/api/lobby?room=" + code)
        assert st == 200 and lob["started"] is True
        # 座位封闭:新名字驳回,同名同机重连放行
        out, st = call("/api/join", {"room": code, "name": "丙", "device_id": "d3"})
        assert st == 409
        out, st = call("/api/join", {"room": code, "name": "甲", "device_id": "d1"})
        assert st == 200 and out["started"] is True
    finally:
        srv.shutdown()
