"""tools/render_demo_assets.py 的口子选路 + DashScope 异步适配器测试。

覆盖:
- 没有 IMAGE_API_BASE、有 DASHSCOPE_API_KEY 时走 DashScope 异步路:
  提交任务(带 X-DashScope-Async 头)→ 轮询任务状态两轮 → SUCCEEDED 取
  output.results[0].url → 下载落盘 → demo_ref 回填。
- FAILED 状态要报错、不落盘。
- 有 IMAGE_API_BASE 时沿用旧 OpenAI 口行为,完全不碰 DashScope(哪怕两把
  key 都配了)。
- 两路 key 都没配时 raise SystemExit,渲染失败(非 SystemExit 的异常)不
  动卡,--dry-run 不发请求。
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from tools import render_demo_assets as mod


CARD = {
    "pattern_id": "pat-v1-02",
    "demo_tier": "T1",
    "demo_prompt": "一张简笔画:五六只手掌心向下依次叠放成一摞小塔。",
}


class _FakeResponse:
    """伪造 urllib.request.urlopen 的上下文管理器返回值。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _json_bytes(obj) -> bytes:
    return json.dumps(obj).encode()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # 轮询间隔不用真等 3 秒
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    # tests/test_modeb_env.py 会直接写 os.environ(不经 monkeypatch),
    # 跑全量 suite 时 IMAGE_API_MODEL 可能被前面的测试串味,这里清空保证本文件内确定性。
    monkeypatch.delenv("IMAGE_API_MODEL", raising=False)


def test_dashscope_path_submits_polls_and_downloads(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGE_API_BASE", raising=False)
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")

    calls = []
    fake_png_bytes = b"\x89PNG-fake-bytes"

    def fake_urlopen(req, timeout=None):
        # 图片下载请求是个裸 str url(下载最终图),不是 Request 对象
        if isinstance(req, str):
            calls.append(("download", req))
            assert req == "https://dashscope-oss.example/result.png"
            return _FakeResponse(fake_png_bytes)

        url = req.full_url
        headers = {k.lower(): v for k, v in req.headers.items()}

        if url == mod.DASHSCOPE_SUBMIT_URL:
            calls.append(("submit", url, headers, req.data))
            assert headers.get("authorization") == "Bearer sk-fake-dashscope"
            # 异步头必须带,且大小写不敏感（urllib 会把 header 存成 Title-Case）
            assert headers.get("x-dashscope-async") == "enable"
            body = json.loads(req.data)
            assert body["model"] == "wan2.7-image-pro"
            assert body["input"]["prompt"].startswith(CARD["demo_prompt"])
            assert body["parameters"]["size"] == "1536*1024"
            return _FakeResponse(_json_bytes({"output": {"task_id": "task-123"}}))

        if url == mod.DASHSCOPE_TASK_URL.format(task_id="task-123"):
            calls.append(("poll", url, headers))
            assert headers.get("authorization") == "Bearer sk-fake-dashscope"
            n_polls = sum(1 for c in calls if c[0] == "poll")
            if n_polls == 1:
                return _FakeResponse(_json_bytes({"output": {"task_status": "RUNNING"}}))
            return _FakeResponse(_json_bytes({
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://dashscope-oss.example/result.png"}],
                }
            }))

        raise AssertionError(f"未预期的请求: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    out_dir = tmp_path
    ref = mod.render_one(CARD, out_dir, dry=False)

    assert ref == "demo/t1/pat-v1-02.png"
    out_path = out_dir / ref
    assert out_path.exists()
    assert out_path.read_bytes() == fake_png_bytes

    kinds = [c[0] for c in calls]
    assert kinds == ["submit", "poll", "poll", "download"]  # 提交 → 轮询两次(第二次成功) → 下载


def test_dashscope_failed_status_raises_and_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGE_API_BASE", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url == mod.DASHSCOPE_SUBMIT_URL:
            return _FakeResponse(_json_bytes({"output": {"task_id": "task-x"}}))
        if url == mod.DASHSCOPE_TASK_URL.format(task_id="task-x"):
            return _FakeResponse(_json_bytes({
                "output": {"task_status": "FAILED", "message": "内容审核未通过"}
            }))
        raise AssertionError(f"未预期的请求: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="DashScope 任务失败"):
        mod.render_one(CARD, tmp_path, dry=False)

    assert not (tmp_path / "demo/t1/pat-v1-02.png").exists()


def test_openai_path_untouched_when_base_set(tmp_path, monkeypatch):
    # 两把 key 都配了:有 IMAGE_API_BASE 时必须走旧口,完全不碰 DashScope。
    monkeypatch.setenv("IMAGE_API_BASE", "https://api.openai.example/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "sk-fake-openai")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-dashscope")

    calls = []
    fake_png_bytes = b"\x89PNG-openai-bytes"

    def fake_urlopen(req, timeout=None):
        assert not isinstance(req, str)  # 不应该触发裸 url 下载分支（DashScope 才会那样调）
        url = req.full_url
        assert "dashscope" not in url  # 硬断言:压根没碰百炼域名
        calls.append(url)
        assert url == "https://api.openai.example/v1/images/generations"
        body = json.loads(req.data)
        assert body["model"] == "gpt-image-2"
        return _FakeResponse(_json_bytes({
            "data": [{"b64_json": __import__("base64").b64encode(fake_png_bytes).decode()}]
        }))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    ref = mod.render_one(CARD, tmp_path, dry=False)
    assert ref == "demo/t1/pat-v1-02.png"
    assert (tmp_path / ref).read_bytes() == fake_png_bytes
    assert calls == ["https://api.openai.example/v1/images/generations"]


def test_no_key_at_all_raises_system_exit(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGE_API_BASE", raising=False)
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        mod.render_one(CARD, tmp_path, dry=False)


def test_dry_run_does_not_call_network(tmp_path, monkeypatch):
    monkeypatch.delenv("IMAGE_API_BASE", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("--dry-run 不应该发任何网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    ref = mod.render_one(CARD, tmp_path, dry=True)
    assert ref is None
    assert not (tmp_path / "demo/t1/pat-v1-02.png").exists()


def test_no_prompt_returns_none(tmp_path):
    ref = mod.render_one({"pattern_id": "pat-empty", "demo_tier": "T1"}, tmp_path, dry=False)
    assert ref is None
