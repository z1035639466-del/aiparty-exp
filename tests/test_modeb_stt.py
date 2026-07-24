"""按住说话(PTT,房主获批 2026-07-24)· /api/stt 验收:fake transport,零真实网络。

覆盖:
- 转写全链:audio_b64 → 全模态口(input_audio 块 + 逐字转写提示词)→
  say(to=局长, via=voice) 走既有 push_event 入队;响应 {ok, text} 回显给说话者;
- 遮蔽不破:别人的 view 只见「跟主持说了句话」(定向发言既有遮蔽形态),本人见全文;
- 转写口不可用(无 key)→ 501「语音通道未接入,打字仍可用」;
- 玩家不在座 → 400;空音频 → 400;
- 音频不留存:episode/公开面无 audio_b64 痕迹,episode 里只有转写后的 say 文本
  (显式动作家族 vs 常驻监听禁区:感知线收束令依然有效,PTT 之外零采集)。
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.simulator import STT_PROMPT, make_server  # noqa: E402

# 音频载荷用可辨认的假字节:留痕检查靠搜这个 base64 串
FAKE_AUDIO_B64 = base64.b64encode(b"fake-ptt-audio-bytes").decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 音频口相关环境变量全清:本文件内确定性(无 key 时 501 的判定不被本机 .env 串味)
    for var in ("AUDIO_JUDGE_BASE", "AUDIO_JUDGE_KEY", "AUDIO_JUDGE_MODEL",
                "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def server(tmp_path):
    srv = make_server(0, tmp_path)  # 端口 0 随机可用端口
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


def _start(base):
    call(base, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                              "objects": [], "driver": "manual"})


class _FakeOmniEar:
    """假全模态口:断言收到 input_audio 块与逐字转写提示词,回一句逐字稿。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, messages):
        self.calls += 1
        assert system == STT_PROMPT, "转写必须用逐字转写提示词(不是裁判口径)"
        blocks = messages[0]["content"]
        audio = next((b for b in blocks if b.get("type") == "input_audio"), None)
        assert audio is not None, "转写口必须收到音频块"
        assert audio["input_audio"]["data"] == FAKE_AUDIO_B64
        assert audio["input_audio"]["format"] == "m4a"
        return "局长这轮算我赢吧"


def test_stt_transcribes_and_queues_say_to_host(server, monkeypatch):
    ear = _FakeOmniEar()
    monkeypatch.setattr("modeb.simulator._make_audio_judge", lambda: ear)
    _start(server)
    res, code = call(server, "/api/stt", {"player": "甲", "audio_b64": FAKE_AUDIO_B64,
                                          "format": "m4a"})
    assert code == 200 and res == {"ok": True, "text": "局长这轮算我赢吧"}, \
        "响应只回 {ok, text} 给说话者回显,不回传音频"
    assert ear.calls == 1

    # 转写文本以 say(to=局长, via=voice) 走既有 push_event 入队(主持从事件流听见)
    snap, _ = call(server, "/api/state")
    says = [e for e in snap["pending_events"] if e.get("type") == "say"]
    assert says and says[0]["player"] == "甲"
    assert says[0]["text"] == "局长这轮算我赢吧"
    assert says[0]["to"] == "局长" and says[0]["via"] == "voice"


def test_stt_masked_for_others_no_audio_trace(server, monkeypatch):
    """别人的 view 只见定向发言遮蔽形态;episode/公开面无音频痕迹。"""
    monkeypatch.setattr("modeb.simulator._make_audio_judge", lambda: _FakeOmniEar())
    _start(server)
    res, code = call(server, "/api/stt", {"player": "甲", "audio_b64": FAKE_AUDIO_B64,
                                          "format": "m4a"})
    assert code == 200
    call(server, "/api/turn", {"text": "收到,继续!", "tool_use": []})  # 事件进回合流水

    view_yi, _ = call(server, f"/api/view?player={quote('乙')}")
    items = [it for t in view_yi["recent"] for it in t["table"]
             if it.get("player") == "甲" and it.get("type") == "say"]
    assert items and items[0].get("note") == "跟主持说了句话", \
        "别人只见「有人跟局长说了话」——与打字的定向发言同一遮蔽形态"
    assert "text" not in items[0], "内容一个字不给别人"
    assert FAKE_AUDIO_B64 not in json.dumps(view_yi, ensure_ascii=False), "公开面不许有音频痕迹"

    view_jia, _ = call(server, f"/api/view?player={quote('甲')}")
    mine = [it for t in view_jia["recent"] for it in t["table"]
            if it.get("player") == "甲" and it.get("type") == "say"]
    assert mine and mine[0].get("text") == "局长这轮算我赢吧", "本人 feed 见自己说的全文"

    # episode:音频转写完即弃,不落盘——文件里只有转写后的 say 文本,和打字的一样
    snap, _ = call(server, "/api/state")
    ep = Path(snap["episode_path"]).read_text(encoding="utf-8")
    assert FAKE_AUDIO_B64 not in ep and "audio_b64" not in ep, "episode 不留任何音频"
    assert "局长这轮算我赢吧" in ep


def test_stt_501_when_no_key(server):
    """转写口不可用(无 key):501 + 明确提示,打字那条路永远在。"""
    _start(server)
    res, code = call(server, "/api/stt", {"player": "甲", "audio_b64": FAKE_AUDIO_B64,
                                          "format": "m4a"})
    assert code == 501
    assert res["error"] == "语音通道未接入,打字仍可用"


def test_stt_rejects_unknown_seat_and_empty_audio(server, monkeypatch):
    monkeypatch.setattr("modeb.simulator._make_audio_judge",
                        lambda: (_ for _ in ()).throw(AssertionError("驳回时不该建转写口")))
    _start(server)
    res, code = call(server, "/api/stt", {"player": "路人", "audio_b64": FAKE_AUDIO_B64})
    assert code == 400 and "未知座位" in res["error"]
    res, code = call(server, "/api/stt", {"player": "甲", "audio_b64": ""})
    assert code == 400 and "audio_b64" in res["error"]
    res, code = call(server, "/api/stt", {"player": "甲"})
    assert code == 400, "缺音频字段同样 400"


def test_stt_502_on_transcribe_failure(server, monkeypatch):
    """转写失败/空稿:502 可重试(App 端不丢录音重发一次),不入任何事件。"""
    class _DeafEar:
        def complete(self, system, messages):
            raise RuntimeError("网断了")
    monkeypatch.setattr("modeb.simulator._make_audio_judge", lambda: _DeafEar())
    _start(server)
    res, code = call(server, "/api/stt", {"player": "甲", "audio_b64": FAKE_AUDIO_B64})
    assert code == 502 and "打字仍可用" in res["error"]
    snap, _ = call(server, "/api/state")
    assert not [e for e in snap["pending_events"] if e.get("type") == "say"], \
        "失败不入事件流(没转出来的话不存在)"
