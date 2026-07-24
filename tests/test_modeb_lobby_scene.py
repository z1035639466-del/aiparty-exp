"""大厅态拍照读场(输入侧去打字化,房主裁定 2026-07-24)。

社交局不许降维成打字游戏:开局场合不再手填,房主开房那一拍拍张现场照,走既有视觉
裁判链路析出 {场合猜测, 实物清单, 场景速写} 存进大厅,lock 时并入 Session——
但**手填优先**,房主开房参数里填了的字段不被拍照覆盖,拍照只填空缺。视觉不可用
(无 key)时明确报错,大厅照常锁定开打(拍照是增强不是门槛)。

风格随 test_modeb_lobby / test_modeb_judge_scene:直接拿 Hub 打 + 一条 HTTP 全链。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.simulator import Hub, make_server  # noqa: E402


def _hub(tmp_path) -> Hub:
    return Hub(tmp_path)


def _open(hub, **cfg):
    """开一个大厅房(players 缺省);默认 mock provider 免得锁定后碰真 LLM。"""
    cfg.setdefault("provider", "mock")
    cfg.setdefault("driver", "manual")
    return hub.start(cfg)


class _FakeVision:
    """假视觉裁判:断言收到了图,回一份 occasion_guess/objects/brief 的读场 JSON。"""

    def __init__(self, occasion="朋友小聚", objects=None, brief="客厅里几个人围着茶几,有酒有零食"):
        self.occasion = occasion
        self.objects = objects if objects is not None else ["啤酒瓶", "扑克牌", "抱枕"]
        self.brief = brief
        self.saw_image = False

    def complete(self, system, messages):
        self.saw_image = any(b.get("type") == "image" for b in messages[0]["content"])
        assert self.saw_image, "读场必须把现场照送进视觉裁判"
        return json.dumps({"occasion_guess": self.occasion,
                           "objects": self.objects, "brief": self.brief},
                          ensure_ascii=False)


class _NoKeyVision:
    """无 key:complete 抛 RuntimeError(与真实 transport 缺 key 时同类)。"""

    def complete(self, system, messages):
        raise RuntimeError("缺 ANTHROPIC_API_KEY(或 ANTHROPIC_AUTH_TOKEN)环境变量")


# —— 1. 房主拍照 → 读场结果存进 Lobby —— #

def test_host_photo_stores_scene_into_lobby(tmp_path, monkeypatch):
    fake = _FakeVision()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: fake)
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]

    out, st = hub.lobby_scene(code, token, None, "aGk=", "image/jpeg")
    assert st == 200 and fake.saw_image
    assert out["occasion_guess"] == "朋友小聚"
    assert set(out["objects"]) == {"啤酒瓶", "扑克牌", "抱枕"}
    assert out["brief"].startswith("客厅")

    lobby = hub.lobbies[code]
    assert lobby.occasion_guess == "朋友小聚"
    assert lobby.scene_brief.startswith("客厅")
    assert lobby.scene_objects == ["啤酒瓶", "扑克牌", "抱枕"]
    # 拍照不建引擎/不落 episode(仍是大厅态)
    assert not list(tmp_path.glob("sim_*.jsonl"))


# —— 2. lock 后并入 Session 三字段 —— #

def test_scene_merges_into_session_on_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _FakeVision())
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]
    hub.lobby_scene(code, token, None, "aGk=", "image/jpeg")
    hub.join(code, "甲", "d1")
    hub.join(code, "乙", "d2")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200
    ses = hub.rooms[code]
    assert ses.state.occasion == "朋友小聚"                      # 场合猜测并入
    assert ses.state.scene_brief.startswith("客厅")             # 场景速写并入
    assert set(ses.state.scene_objects) == {"啤酒瓶", "扑克牌", "抱枕"}  # 实物清单并入


# —— 3. 手填优先于拍照(拍照只填空缺)—— #

def test_host_typed_fields_win_over_photo(tmp_path, monkeypatch):
    """开房 cfg 手填了 occasion / objects:拍照不覆盖;没填的 scene_brief 才由拍照填。
    objects 两边都留:手填排前、拍照去重补后。"""
    monkeypatch.setattr(
        "modeb.simulator.make_transport",
        lambda *a, **k: _FakeVision(occasion="办公室团建", objects=["投影", "抱枕"],
                                    brief="办公室工位区"))
    hub = _hub(tmp_path)
    # 房主手填 occasion + objects(scene_brief 留空)
    op = _open(hub, device_id="host-dev", occasion="老王生日局", objects=["蛋糕", "抱枕"])
    code, token = op["room_code"], op["host_token"]
    hub.lobby_scene(code, token, None, "aGk=", "image/jpeg")
    hub.join(code, "甲", "d1")
    hub.join(code, "乙", "d2")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200
    ses = hub.rooms[code]
    assert ses.state.occasion == "老王生日局", "手填的场合不被拍照覆盖"
    assert ses.state.scene_brief == "办公室工位区", "手填没填的速写由拍照填空缺"
    # objects:手填在前,拍照认出的去重补后(抱枕重复只留一份)
    assert ses.state.scene_objects == ["蛋糕", "抱枕", "投影"]


# —— 4. 非房主拍照驳回 —— #

def test_non_host_photo_rejected(tmp_path, monkeypatch):
    fake = _FakeVision()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: fake)
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]
    # 错 token + 非开房设备 → 403,视觉裁判根本没被调用,大厅态未动
    out, st = hub.lobby_scene(code, "假token", "路人机", "aGk=", "image/jpeg")
    assert st == 403 and "房主" in out["error"]
    assert not fake.saw_image, "非房主直接驳回,不该烧视觉调用"
    assert hub.lobbies[code].occasion_guess == "", "被驳回不改动大厅态"
    # 凭开房 device_id 认人(无 token)→ 放行
    out, st = hub.lobby_scene(code, None, "host-dev", "aGk=", "image/jpeg")
    assert st == 200 and out["occasion_guess"] == "朋友小聚"


# —— 5. 无 key 明确报错,且不挡锁定 —— #

def test_no_key_clear_error_and_lock_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _NoKeyVision())
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]

    out, st = hub.lobby_scene(code, token, None, "aGk=", "image/jpeg")
    assert st == 502 and "error" in out
    assert "视觉裁判不可用" in out["error"] and "锁定开打" in out["error"]
    # 大厅态一个字段都没动
    lobby = hub.lobbies[code]
    assert lobby.occasion_guess == "" and lobby.scene_brief == "" and lobby.scene_objects == []
    # 拍照失败照样能锁定开打(拍照是增强不是门槛)
    hub.join(code, "甲", "d1")
    hub.join(code, "乙", "d2")
    snap, st = hub.lock_room(code, token, None)
    assert st == 200 and code in hub.rooms
    ses = hub.rooms[code]
    assert ses.state.occasion == "" and ses.state.scene_objects == []


# —— 6. 缺图 400;已开打的房不收(读场只在大厅态)—— #

def test_missing_image_and_started_room_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _FakeVision())
    hub = _hub(tmp_path)
    op = _open(hub, device_id="host-dev")
    code, token = op["room_code"], op["host_token"]
    out, st = hub.lobby_scene(code, token, None, None, "image/jpeg")
    assert st == 400 and "image_b64" in out["error"]
    # 锁定后再拍 → 转投进行中的局(竞速洞修复 2026-07-24:拍照分析比手快的房主慢,
    # 迟到的侦察结果不许丢——并进 Session 并重建主持 system)
    hub.join(code, "甲", "d1")
    hub.join(code, "乙", "d2")
    hub.lock_room(code, token, None)
    session = hub.rooms[code]
    out, st = hub.lobby_scene(code, token, None, "aGk=", "image/jpeg")
    assert st == 200, out
    assert set(out["objects"]) <= set(session.state.scene_objects)
    # 开打后缺图照旧 400
    out, st = hub.lobby_scene(code, token, None, None, "image/jpeg")
    assert st == 400


# —— 7. HTTP 全链:/api/start(空) → /api/lobby_scene → /api/join → /api/lock —— #

def test_lobby_scene_full_flow_over_http(tmp_path, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport",
                        lambda *a, **k: _FakeVision(occasion="生日趴", objects=["气球", "蛋糕"],
                                                    brief="挂了拉花的客厅"))
    srv = make_server(0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
        op, st = call("/api/start", {"provider": "mock", "driver": "manual",
                                     "device_id": "host-dev"})
        assert st == 200 and op["lobby"] is True
        code, token = op["room_code"], op["host_token"]
        # 非房主拍照 403
        out, st = call("/api/lobby_scene", {"room": code, "host_token": "假",
                                            "device_id": "路人", "image_b64": "aGk="})
        assert st == 403
        # 房主拍照读场
        sc, st = call("/api/lobby_scene", {"room": code, "host_token": token,
                                           "image_b64": "aGk=", "media_type": "image/jpeg"})
        assert st == 200 and sc["occasion_guess"] == "生日趴"
        assert set(sc["objects"]) == {"气球", "蛋糕"}
        # 入座 → 锁定 → 读场三字段进 Session
        call("/api/join", {"room": code, "name": "甲", "device_id": "d1"})
        call("/api/join", {"room": code, "name": "乙", "device_id": "d2"})
        snap, st = call("/api/lock", {"room": code, "host_token": token})
        assert st == 200
        assert snap["digest"]["scene_objects"] == ["气球", "蛋糕"], "锁定快照带上并入的实物"
    finally:
        srv.shutdown()
