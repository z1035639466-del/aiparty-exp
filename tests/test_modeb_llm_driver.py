"""M2 第一单验收:LLM 驱动器消息组装/解析/容错(MockTransport,不碰网络)。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.driver_llm import (  # noqa: E402
    FALLBACK, LLMDriver, MockTransport, parse_decision,
)
from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402

PLAYERS = ["疯子明", "小静", "大鹏"]


def _driver(responses):
    return LLMDriver(MockTransport(responses), PLAYERS, wildness_cap=6, time_budget_min=30)


def test_system_prompt_carries_iron_rules_and_table():
    d = _driver([])
    for token in ["疯子明", "野度档:6", "时长预算:30", "钳制层", "输出契约", "draw_atom"]:
        assert token in d.system


def test_parse_clips_tools_to_two():
    raw = json.dumps({"text": "好!", "tool_use": [
        {"name": "state.next_round", "input": {}},
        {"name": "fx", "input": {"effect": "x"}},
        {"name": "timer", "input": {"seconds": 10}}]}, ensure_ascii=False)
    out = parse_decision(raw)
    assert len(out["tool_use"]) == 2


def test_parse_tolerates_prose_wrapping():
    raw = '当然!这是我的决定:{"text": "开局!", "tool_use": []} 以上。'
    assert parse_decision(raw)["text"] == "开局!"


def test_malformed_retries_then_fallback():
    d = _driver(["这不是JSON", "还不是JSON"])  # 首答坏 + 重试坏 → 兜底
    out = d.decide({"round": 0, "scores": {}}, [])
    assert out == FALLBACK
    assert d.malformed_count == 2


def test_malformed_once_then_recovers():
    good = json.dumps({"text": "来!", "tool_use": [{"name": "state.next_round", "input": {}}]}, ensure_ascii=False)
    d = _driver(["坏格式", good])
    out = d.decide({"round": 0, "scores": {}}, [])
    assert out["text"] == "来!"
    assert d.malformed_count == 1


def test_turn_message_and_history_window():
    good = json.dumps({"text": "继续", "tool_use": []}, ensure_ascii=False)
    d = _driver([good] * 10)
    for i in range(10):
        d.decide({"round": i, "scores": {}}, [{"type": "laugh"}])
    sent = d.transport.calls[-1]["messages"]
    assert len(sent) <= 6 * 2 + 1, "历史窗口须截断"
    payload = json.loads(sent[-1]["content"])
    assert payload["state_digest"]["round"] == 9
    assert payload["events"] == [{"type": "laugh"}]


def test_llm_driver_runs_full_game_through_engine(tmp_path):
    """同一引擎不换一行代码,换上 LLM 驱动器(mock 剧本)也能完局——插座证明。"""
    script = [
        {"text": "开局!", "tool_use": [{"name": "state.next_round", "input": {}}]},
        {"text": "抽一个。", "tool_use": [{"name": "draw_atom", "input": {"野度": 5}}]},
        {"text": "收工,合影!", "tool_use": [{"name": "state.finish", "input": {}}]},
    ]
    d = _driver([json.dumps(s, ensure_ascii=False) for s in script])
    state = GameState(players=PLAYERS, wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子", "冰块", "手机", "杯子"])
    eng = Engine(state, d, tmp_path / "ep.jsonl", rng_seed=1)
    summary = eng.run(max_turns=10)
    assert state.finished and summary["turns"] == 3
