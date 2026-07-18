"""M1 验收:一局跑通,episode 完整落盘,钳制层生效,埋点在账。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.driver_scripted import ScriptedDriver  # noqa: E402
from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


def _run_game(tmp_path: Path):
    state = GameState(
        players=["疯子明", "小静", "大鹏"], wildness_cap=6, time_budget_min=30,
        scene_objects=["瓶子", "冰块", "纸巾", "手机", "杯子", "打火机"])
    ep = tmp_path / "ep.jsonl"
    eng = Engine(state, ScriptedDriver(), ep, rng_seed=7)
    feed = {
        3: [{"type": "vote_result", "pass": True, "tally": "2:1"}, {"type": "laugh"}],
        5: [{"type": "ritual_done", "prop": "决斗手套"}, {"type": "laugh"}, {"type": "laugh"}],
        6: [{"type": "pass", "player": "大鹏"}],
        8: [{"type": "laugh"}],
    }
    while not state.finished and eng.marks["turns"] < 60:
        for ev in feed.get(eng.marks["turns"], []):
            eng.push_event(ev)
        eng.turn()
    summary = eng.run(max_turns=eng.marks["turns"])
    return state, ep, summary


def test_full_game_completes(tmp_path):
    state, ep, summary = _run_game(tmp_path)
    assert state.finished, "一局必须走到终局"
    assert summary["turns"] == 10


def test_episode_lines_valid(tmp_path):
    _, ep, _ = _run_game(tmp_path)
    lines = [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["episode_summary"] is True
    for line in lines[:-1]:
        assert {"turn", "digest", "events_in", "text", "tool_use", "results", "ledger_diff"} <= set(line)
        assert len(line["tool_use"]) <= 2, "每回合工具调用钳制 ≤2"


def test_clamp_rejects_out_of_bound_score(tmp_path):
    state, _, summary = _run_game(tmp_path)
    clamps = summary["clamps"]
    assert any("写分越界" in c["clamped"] for c in clamps), "越界写分必须被拒并留痕"
    assert state.scores["小静"] == 0, "被拒的写分不得落账"


def test_ledger_only_via_state_tool(tmp_path):
    _, ep, _ = _run_game(tmp_path)
    lines = [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines()][:-1]
    for line in lines:
        if line["ledger_diff"]:
            assert any(c["name"].startswith("state.") for c in line["tool_use"]), \
                "账本变动必须来自 state 工具调用"


def test_skill_grant_and_use(tmp_path):
    from modeb.atoms_seed import SEED_ATOMS
    state, _, _ = _run_game(tmp_path)
    assert state.grants, "第 4 步必须完成一次技能授予"
    g = state.grants[-1]
    assert g.holder == "小静", "grant_to 指定给了小静"
    original = next(a["skill"]["uses"] for a in SEED_ATOMS if "skill" in a and a["skill"]["prop"] == g.prop)
    assert g.uses_left == original - 1, "第 5 步发动一次后次数应减一"


def test_marks_recorded(tmp_path):
    _, _, summary = _run_game(tmp_path)
    assert summary["laugh_events"] == 4
    assert summary["skips"] == 1
    assert summary["would_replay_yes"] is None, "复玩问卷局后填,不得预填"
