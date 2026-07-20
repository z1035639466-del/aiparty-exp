"""AI 局头当 DJ:歌单是房主上传的资产(真人可写、AI 只读只调)。

模型只发「放这首」的意图,播放由运行时执行;点歌单外的歌 = 钳制——
与 demo_ref 资产册同一姿势,防主持幻觉出一首不存在的歌。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.driver_llm import build_system_prompt  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402

SONGS = ["Mojito - 周杰伦", "APT. - ROSÉ", "晴天 - 周杰伦", "Dancing Queen - ABBA"]


def _executor(playlist=None):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      playlist=list(playlist or []))
    return ToolExecutor(state)


def test_play_by_substring_sets_now_playing():
    ex = _executor(SONGS)
    r = ex.execute({"name": "music.play", "input": {"track": "Mojito", "mood": "热场"}})
    assert r["ok"] and r["result"]["playing"] == "Mojito - 周杰伦"
    assert ex.state.now_playing == "Mojito - 周杰伦"
    assert ex.state.digest(30.0)["now_playing"] == "Mojito - 周杰伦", "digest 要带正在放的"


def test_hallucinated_track_is_clamped():
    ex = _executor(SONGS)
    r = ex.execute({"name": "music.play", "input": {"track": "水星记"}})
    assert r["ok"] is False and "歌单里没有" in r["clamped"]
    assert ex.state.now_playing is None, "钳制掉的点歌不得改状态"


def test_ambiguous_track_is_clamped():
    ex = _executor(SONGS)
    r = ex.execute({"name": "music.play", "input": {"track": "周杰伦"}})
    assert r["ok"] is False and "撞名" in r["clamped"]


def test_no_playlist_means_no_dj():
    r = _executor().execute({"name": "music.play", "input": {"track": "Mojito"}})
    assert r["ok"] is False and "没有歌单" in r["clamped"]


def test_stop():
    ex = _executor(SONGS)
    ex.execute({"name": "music.play", "input": {"track": "晴天"}})
    r = ex.execute({"name": "music.stop", "input": {}})
    assert r["ok"] and r["result"]["stopped"] == "晴天 - 周杰伦"
    assert ex.state.now_playing is None


def test_dj_section_only_when_playlist_uploaded():
    with_dj = build_system_prompt(["甲", "乙"], 6, 30, playlist=SONGS)
    assert "【DJ 台】" in with_dj and "Mojito - 周杰伦" in with_dj
    assert "音乐是背景不是主持词" in with_dj
    without = build_system_prompt(["甲", "乙"], 6, 30)
    assert "【DJ 台】" not in without, "没歌单就不给 DJ 指令,省 token 也免得它想放歌"
