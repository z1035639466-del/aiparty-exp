"""零依赖 .env 加载:配一次永久生效;已在环境里的变量优先(export 可覆盖)。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modeb.env as envmod  # noqa: E402


def _fresh_load(tmp_path, text, monkeypatch):
    monkeypatch.setattr(envmod, "_LOADED", False)  # 重置幂等闸,允许重复测
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    envmod.load_env(p)


def test_loads_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIO_JUDGE_MODEL", raising=False)
    _fresh_load(tmp_path, 'AUDIO_JUDGE_MODEL=qwen-omni-turbo\n# 注释\nIMAGE_API_MODEL="gpt-image-2"\n', monkeypatch)
    import os
    assert os.environ["AUDIO_JUDGE_MODEL"] == "qwen-omni-turbo"
    assert os.environ["IMAGE_API_MODEL"] == "gpt-image-2", "引号要剥掉"


def test_existing_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_API_KEY", "from-shell")
    _fresh_load(tmp_path, "IMAGE_API_KEY=from-file\n", monkeypatch)
    import os
    assert os.environ["IMAGE_API_KEY"] == "from-shell", "已在环境里的优先,export 可临时覆盖"


def test_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(envmod, "_LOADED", False)
    envmod.load_env(tmp_path / "nope.env")  # 不存在不报错
