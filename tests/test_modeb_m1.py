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
    from modeb.tools import load_atom_pool  # 技能已单独开库,查全池不只查种子
    state, _, _ = _run_game(tmp_path)
    assert state.grants, "第 4 步必须完成一次技能授予"
    g = state.grants[-1]
    assert g.holder == "小静", "grant_to 指定给了小静"
    original = next(a["skill"]["uses"] for a in load_atom_pool()
                    if a.get("skill") and a["skill"]["prop"] == g.prop)
    assert g.uses_left == original - 1, "第 5 步发动一次后次数应减一"


def test_marks_recorded(tmp_path):
    _, _, summary = _run_game(tmp_path)
    assert summary["laugh_events"] == 4
    assert summary["skips"] == 1
    assert summary["would_replay_yes"] is None, "复玩问卷局后填,不得预填"


def test_three_signals_counted(tmp_path):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=1)
    eng.push_event({"type": "done", "player": "甲"})
    eng.push_event({"type": "forfeit", "player": "乙"})
    eng.push_event({"type": "optout", "player": "丙"})
    assert eng.marks["forfeits"] == 1, "认罚跳过单独计数(正常游戏动作)"
    assert eng.marks["skips"] == 1, "安全退出计入skips(底线信号,迭代要盯)"


def test_turn_ready_event_driven(tmp_path):
    """房主裁定「没回应就等」:无事件无到点计时器则不叫醒主持。"""
    import time as _time
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=1)
    assert eng.turn_ready(), "开局首拍必 ready"
    eng.turn()
    assert not eng.turn_ready(), "桌上没动静就不该打扰主持"
    eng.push_event({"type": "laugh"})
    assert eng.turn_ready(), "有新事件即 ready"
    eng.turn()
    state.timers.append(_time.time() - 0.1)
    assert eng.turn_ready(), "计时器到点即 ready"
    line = eng.turn()
    assert {"type": "timer_expired"} in line["events_in"], "到点计时器须作为事件进回合"
    assert all(t > _time.time() for t in state.timers), "到点的被消费,未到点的保留"


def test_opener_atoms_available(tmp_path):
    from modeb.atoms_seed import SEED_ATOMS
    openers = [a for a in SEED_ATOMS if a.get("opener")]
    assert {a["name"] for a in openers} == {"吹牛骰", "十五二十", "石头剪刀布擂台", "抓手指", "分队车轮战", "快枪手对决"}
    assert all(not a["props"] or a["props"] == ["手机"] for a in openers), \
        "通用局须零道具或仅手机(手机人手一台视同零道具)"


def test_host_prompt_carries_new_iron_rules():
    from modeb.driver_llm import build_system_prompt
    sp = build_system_prompt(["甲", "乙"], 6, 30)
    for token in ["静静等", "一个字都不许", "吹牛骰", "不要汇总排名", "怂货榜", "不念名次", "快枪手",
                  "认罚跳过", "安全退出", "不追问不起哄不渲染",
                  "不许包装", "只换措辞、换个说法指同一件事,不是改版", "骨架**原样保留**",
                  "野度min 真加档", "不搞固定仪式、不搞合影环节"]:
        assert token in sp
    assert "加冕礼" not in sp, "固定终局仪式已废除(房主裁定:合影很扯)"
