"""读场(occasion)+ 拍照判定(多模态判定通道 v0)。

读场:没有场合输入,"自动读场"无场可读,局局跑成通用娱乐局;主持开场必须
播报它读到的场——读了不说,房主感觉不到调整。
拍照判定:显式发起的判定时刻(感知线收束裁定),非常驻监听;判不了明说
「无法判定」,主持走 ask 共识兜底。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.driver_llm import build_system_prompt  # noqa: E402
from modeb.simulator import make_server  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


# —— 读场 ——

def test_occasion_injects_scene_reading_rules():
    p = build_system_prompt(["甲", "乙"], 6, 30, occasion="生日局", scene_brief="家里,有投影")
    assert "【读场】" in p and "生日局" in p and "有投影" in p
    assert "不播报不解释" in p, "读场是暗调(房主裁定:播报设定破坏体验)"
    assert "房主中途一句话" in p, "修正权留给房主"
    assert "【读场】" not in build_system_prompt(["甲", "乙"], 6, 30), "没输入不注水"


# —— judge.photo 工具 ——

def test_judge_photo_tool_and_digest():
    ex = ToolExecutor(GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30))
    assert not ex.execute({"name": "judge.photo", "input": {"player": "路人", "prompt": "x"}})["ok"]
    assert not ex.execute({"name": "judge.photo", "input": {"player": "甲"}})["ok"], "缺标准不收单"
    r = ex.execute({"name": "judge.photo", "input": {"player": "甲", "prompt": "发型像不像JoJo"}})
    assert r["ok"] and ex.state.digest(30.0)["photo_wait"] == "甲"
    again = ex.execute({"name": "judge.photo", "input": {"player": "乙", "prompt": "y"}})
    assert not again["ok"] and "进行中" in again["clamped"], "一次一单"
    assert ex.execute({"name": "judge.cancel", "input": {}})["result"]["cancelled"] is True


# —— HTTP 全链(假视觉裁判) ——

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


class _FakeVision:
    def complete(self, system, messages):
        assert any(b.get("type") == "image" for b in messages[0]["content"]), "裁判必须收到图"
        return '{"verdict": "过", "reason": "发型确实很JoJo"}'


def test_photo_judge_full_chain(server, monkeypatch):
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _FakeVision())
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": [], "driver": "manual"})
    _, code = call(server, "/api/photo", {"player": "甲", "image_b64": "x"})
    assert code == 409, "没出题不收照片"
    call(server, "/api/turn", {"text": "验收发型!", "tool_use": [
        {"name": "judge.photo", "input": {"player": "甲", "prompt": "发型像不像JoJo"}}]})

    view_jia, _ = call(server, "/api/view?player=%E7%94%B2")
    view_yi, _ = call(server, "/api/view?player=%E4%B9%99")
    assert view_jia["photo_request"] == "发型像不像JoJo"
    assert view_yi["photo_request"] is None, "出题只给被点名的人"

    _, code = call(server, "/api/photo", {"player": "乙", "image_b64": "x"})
    assert code == 403, "别人不能替他交卷"

    res, code = call(server, "/api/photo", {"player": "甲", "image_b64": "aGk=",
                                            "media_type": "image/jpeg"})
    assert code == 200 and res["verdict"] == "过" and "JoJo" in res["reason"]

    snap, _ = call(server, "/api/state")
    assert snap["digest"]["photo_wait"] is None, "结案清单"
    evs = [(e.get("type"), e.get("verdict")) for e in snap["pending_events"]]
    assert ("judge_result", "过") in evs, "判定结果要叫醒主持宣布"


# —— judge.photo frames(视频抽帧判定通道)——

class _FakeVisionCountFrames:
    """假视觉裁判:记下收到几张图,断言这是"连续抽帧"而非单张照片。"""

    def __init__(self) -> None:
        self.image_count = 0
        self.saw_frames_hint = False

    def complete(self, system, messages):
        content = messages[0]["content"]
        images = [b for b in content if b.get("type") == "image"]
        self.image_count = len(images)
        text = next((b["text"] for b in content if b.get("type") == "text"), "")
        self.saw_frames_hint = "连续抽帧" in text
        return '{"verdict": "过", "reason": "动作连贯,过"}'


def test_photo_judge_accepts_video_frames(server, monkeypatch):
    """视频判定=抽帧走照片通道:客户端把短视频抽成几帧,服务端多 image block 一起送审,
    其余流程(结案/judge_result/403/409)与单张照片判定完全一致。"""
    fake = _FakeVisionCountFrames()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: fake)
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": [], "driver": "manual"})
    call(server, "/api/turn", {"text": "验收这段扭腰!", "tool_use": [
        {"name": "judge.photo", "input": {"player": "甲", "prompt": "扭腰动作是否到位"}}]})

    res, code = call(server, "/api/photo", {"player": "甲",
                                            "frames": ["aGk=", "aGk1", "aGk2"],
                                            "media_type": "image/jpeg"})
    assert code == 200 and res["verdict"] == "过" and "连贯" in res["reason"]
    assert fake.image_count == 3, "假裁判应收到 3 帧,而不是 1 张照片"
    assert fake.saw_frames_hint, "裁判提示词要点明这是同一段动作的连续抽帧"

    snap, _ = call(server, "/api/state")
    assert snap["digest"]["photo_wait"] is None, "视频判定也要正常结案"
    evs = [(e.get("type"), e.get("verdict")) for e in snap["pending_events"]]
    assert ("judge_result", "过") in evs, "判定结果要叫醒主持宣布"


def test_photo_judge_frames_target_and_pending_rules_unchanged(server, monkeypatch):
    """frames 路径复用同一套 403/409 校验,不是另开一条口子。"""
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _FakeVisionCountFrames())
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": [], "driver": "manual"})
    _, code = call(server, "/api/photo", {"player": "甲", "frames": ["x"]})
    assert code == 409, "没出题不收视频帧"

    call(server, "/api/turn", {"text": "验收!", "tool_use": [
        {"name": "judge.photo", "input": {"player": "甲", "prompt": "标准"}}]})
    _, code = call(server, "/api/photo", {"player": "乙", "frames": ["x"]})
    assert code == 403, "别人不能替他交视频"


def test_photo_judge_frames_and_image_mutually_exclusive(server):
    """image_b64 与 frames 互斥:同时给等于客户端逻辑错了,明确拒绝而非各挑一个悄悄用。"""
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": [], "driver": "manual"})
    call(server, "/api/turn", {"text": "验收!", "tool_use": [
        {"name": "judge.photo", "input": {"player": "甲", "prompt": "标准"}}]})
    res, code = call(server, "/api/photo", {"player": "甲", "image_b64": "x", "frames": ["y"]})
    assert code == 400 and "二选一" in res["error"]


def test_photo_judge_frames_validation(server):
    """frames 形状校验:空列表、超过 5 帧,都该 400,不该悄悄放行或 500。"""
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": [], "driver": "manual"})
    call(server, "/api/turn", {"text": "验收!", "tool_use": [
        {"name": "judge.photo", "input": {"player": "甲", "prompt": "标准"}}]})

    res, code = call(server, "/api/photo", {"player": "甲", "frames": []})
    assert code == 400, "空 frames 列表不该放行"

    res, code = call(server, "/api/photo", {"player": "甲", "frames": ["a"] * 6})
    assert code == 400 and "5" in res["error"], "超过 5 帧要拒绝"
