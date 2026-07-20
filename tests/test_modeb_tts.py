"""局长开口(modeb/tts.py + /api/tts)验收:fake urlopen,零真实网络。

覆盖:
- synthesize 请求体构造正确(URL=base+TTS_PATH、Bearer 鉴权、model/input.text/voice);
- key 回落链(TTS_API_KEY 优先 → DASHSCOPE_API_KEY)与 base 回落链(TTS_BASE_URL 优先);
- 响应两形态:output.audio.data 内联 base64 / output.audio.url 下载;
- 没 key 时 synthesize 抛 TTSError、/api/tts 回 404 说明(不报错不崩);
- (room,line) 缓存:同一句拉两次只烧一次;不带 line 默认最新主持拍。
"""
from __future__ import annotations

import base64
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb import tts  # noqa: E402
from modeb.simulator import make_server  # noqa: E402

FAKE_MP3 = b"\xff\xf3fake-mp3-bytes"

# 真 urlopen 存一份:endpoint 测试里 fake 只截胡 dashscope 域名,
# 本机测试服务器的请求原样放行(测试客户端与 TTS 出口共用 urllib)。
REAL_URLOPEN = urllib.request.urlopen


class _FakeResponse:
    """伪造 urlopen 的上下文管理器返回值(与 test_render_assets 同款)。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # tts 相关环境变量全清,保证本文件内确定性(全量 suite 不串味)
    for var in ("TTS_API_KEY", "TTS_BASE_URL", "TTS_MODEL", "TTS_VOICE",
                "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


# —— synthesize 本体 —— #

def test_synthesize_builds_dashscope_payload(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        assert req.full_url == tts.DASHSCOPE_BASE + tts.TTS_PATH
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers.get("authorization") == "Bearer sk-fake-dashscope"
        body = json.loads(req.data)
        assert body["model"] == tts.DEFAULT_MODEL          # qwen3-tts
        assert body["input"][tts.FIELD_TEXT] == "举杯!这拍归玩家3!"
        assert body["input"][tts.FIELD_VOICE] == tts.DEFAULT_VOICE
        return _FakeResponse(json.dumps({"output": {"audio": {
            "data": base64.b64encode(FAKE_MP3).decode()}}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = tts.synthesize("举杯!这拍归玩家3!")
    assert out == FAKE_MP3
    assert len(seen) == 1  # 内联 base64:一次往返拿到音频,无二次下载


def test_synthesize_env_overrides_and_url_download(monkeypatch):
    # 专口三件套优先:TTS_API_KEY / TTS_BASE_URL / TTS_MODEL / TTS_VOICE 全覆盖
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-should-not-win")
    monkeypatch.setenv("TTS_API_KEY", "sk-tts-wins")
    monkeypatch.setenv("TTS_BASE_URL", "https://my-space.cn-beijing.example/")
    monkeypatch.setenv("TTS_MODEL", "qwen3-tts-flash")
    monkeypatch.setenv("TTS_VOICE", "Ethan")
    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith(tts.TTS_PATH):
            calls.append("synth")
            assert url == "https://my-space.cn-beijing.example" + tts.TTS_PATH
            headers = {k.lower(): v for k, v in req.headers.items()}
            assert headers.get("authorization") == "Bearer sk-tts-wins"
            body = json.loads(req.data)
            assert body["model"] == "qwen3-tts-flash"
            assert body["input"][tts.FIELD_VOICE] == "Ethan"
            # 这回走 url 形态:音频在 OSS 上,得再下载一趟
            return _FakeResponse(json.dumps({"output": {"audio": {
                "url": "https://oss.example/host-line.mp3"}}}).encode())
        calls.append("download")
        assert url == "https://oss.example/host-line.mp3"
        return _FakeResponse(FAKE_MP3)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert tts.synthesize("下一拍!") == FAKE_MP3
    assert calls == ["synth", "download"]


def test_synthesize_without_key_raises_ttserror(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("没 key 不应该发任何网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert not tts.configured()
    with pytest.raises(tts.TTSError, match="TTS 未接入"):
        tts.synthesize("有词也不念")


def test_synthesize_http_error_becomes_ttserror(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None,
                                     io.BytesIO(b'{"code":"InvalidApiKey"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(tts.TTSError) as ei:
        tts.synthesize("念一句")
    assert ei.value.code == 401  # TransportError 同款:状态码带出,定位不靠猜


# —— /api/tts 端点 —— #

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
        with REAL_URLOPEN(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def get_audio(base, path):
    """拉音频口:返回 (原始字节, 状态码, Content-Type);错误时字节是 JSON 正文。"""
    try:
        with REAL_URLOPEN(base + path) as r:
            return r.read(), r.status, r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.read(), e.code, e.headers.get("Content-Type")


def _start_and_speak(base, text="各位!举杯!"):
    call(base, "/api/start", {"players": ["玩家1", "玩家2", "玩家3"],
                              "minutes": 30, "wildness": 6, "driver": "manual"})
    line, code = call(base, "/api/turn", {"text": text, "tool_use": []})
    assert code == 200
    return line


def test_endpoint_404_when_unconfigured(server, monkeypatch):
    def boom(req, timeout=None):
        raise AssertionError("未配 key 时 /api/tts 不应该发任何 TTS 请求")

    monkeypatch.setattr(tts.urllib.request, "urlopen", boom)   # 截 tts 出口即可
    line = _start_and_speak(server)
    body, code = call(server, f"/api/tts?line={line['turn']}")
    assert code == 404, "TTS 未配置须回 404 说明,不报错不崩"
    assert "TTS_API_KEY" in body["error"] or "DASHSCOPE_API_KEY" in body["error"]
    # 局照跑:404 之后回合功能完好
    _, code = call(server, "/api/turn", {"text": "继续!", "tool_use": []})
    assert code == 200


def test_endpoint_synthesizes_and_caches(server, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")
    synth_calls = []

    def fake_urlopen(req, timeout=None):
        # 只截胡 TTS 出口;测试客户端连本机服务器的请求原样放行
        url = req if isinstance(req, str) else req.full_url
        if "127.0.0.1" in url:
            return REAL_URLOPEN(req, timeout=timeout) if timeout else REAL_URLOPEN(req)
        assert url == tts.DASHSCOPE_BASE + tts.TTS_PATH
        body = json.loads(req.data)
        synth_calls.append(body["input"][tts.FIELD_TEXT])
        return _FakeResponse(json.dumps({"output": {"audio": {
            "data": base64.b64encode(FAKE_MP3).decode()}}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    line1 = _start_and_speak(server, "第一拍,自我介绍!")

    audio, code, ctype = get_audio(server, f"/api/tts?line={line1['turn']}")
    assert (code, audio, ctype) == (200, FAKE_MP3, tts.TTS_MIME)
    assert synth_calls == ["第一拍,自我介绍!"]

    # 缓存命中:同一句再拉,不二次烧钱
    audio2, code2, _ = get_audio(server, f"/api/tts?line={line1['turn']}")
    assert (code2, audio2) == (200, FAKE_MP3)
    assert synth_calls == ["第一拍,自我介绍!"], "同 (room,line) 第二次拉必须走缓存"

    # 不带 line 默认最新一条有词的主持拍;新句子才再烧一次
    line2, _ = call(server, "/api/turn", {"text": "第二拍,交换外号!", "tool_use": []})
    audio3, code3, _ = get_audio(server, "/api/tts")
    assert (code3, audio3) == (200, FAKE_MP3)
    assert synth_calls == ["第一拍,自我介绍!", "第二拍,交换外号!"]
    # 默认最新与显式 line=N 命中同一格缓存
    get_audio(server, f"/api/tts?line={line2['turn']}")
    assert len(synth_calls) == 2


def test_endpoint_bad_line_params(server, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")
    monkeypatch.setattr(tts.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该发请求")))
    _start_and_speak(server)
    body, code = call(server, "/api/tts?line=abc")
    assert code == 400 and "line" in body["error"]
    body, code = call(server, "/api/tts?line=99")
    assert code == 404, "不存在的回合号:404,不合成"
