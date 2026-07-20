"""发言分离(M2 实测改进项):「对局长说」与「桌上互说」两路。

现实里天然分开,只有 agent 桌混着。规则:
- to=局长:定向频道,任何感知档全文送达,必叫醒主持;
- to=桌上(默认):转写档全文;按钮档降级「有人说话」且不叫醒——
  过去按钮档也全文送,等于漏音;叫醒聋主持只会逼它对空气编话。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.player_agent import parse_player_events  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _IdleDriver:
    def decide(self, digest, events):
        return {"text": "", "tool_use": []}


def _engine(tmp_path, perception):
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      host_perception=perception)
    return Engine(state, _IdleDriver(), tmp_path / "ep.jsonl")


def test_button_tier_splits_channels(tmp_path):
    eng = _engine(tmp_path, "按钮")
    out = eng._perceive([
        {"type": "say", "player": "甲", "text": "乙你上次也输了吧哈哈"},
        {"type": "say", "player": "乙", "text": "局长这规则算平局吗", "to": "局长"},
        {"type": "laugh", "player": "丙"},
    ])
    assert out[0] == {"type": "say", "player": "甲", "inaudible": True}, \
        "按钮档桌上互说必须降级成听不见"
    assert out[1]["text"] == "局长这规则算平局吗", "对局长说任何档全文送达"
    assert out[2]["type"] == "laugh", "非 say 事件不受影响"


def test_transcript_tier_hears_everything(tmp_path):
    eng = _engine(tmp_path, "转写")
    evs = [{"type": "say", "player": "甲", "text": "悄悄话"},
           {"type": "say", "player": "乙", "text": "报告", "to": "局长"}]
    assert eng._perceive(evs) == evs, "转写档 = ASR 听得见全场,两路都全文"


def test_button_tier_table_talk_does_not_wake_host(tmp_path):
    eng = _engine(tmp_path, "按钮")
    eng.turn()  # 消耗开局首拍(首拍无条件 ready)
    assert not eng.turn_ready(), "无事件不该醒"
    eng.push_event({"type": "say", "player": "甲", "text": "闲聊"})
    assert not eng.turn_ready(), "按钮档桌上闲聊是背景噪音,不烧回合"
    eng.push_event({"type": "say", "player": "乙", "text": "申诉!", "to": "局长"})
    assert eng.turn_ready(), "对局长说必叫醒"


def test_transcript_tier_table_talk_wakes_host(tmp_path):
    eng = _engine(tmp_path, "转写")
    eng.turn()
    eng.push_event({"type": "say", "player": "甲", "text": "闲聊"})
    assert eng.turn_ready(), "转写档主持听得见,桌上互说照常叫醒"


def test_bot_say_channel_normalization():
    raw = json.dumps({"events": [
        {"type": "say", "text": "问个规则", "to": "局长"},
        {"type": "say", "text": "起哄", "to": "全宇宙"},
    ]}, ensure_ascii=False)
    evs = parse_player_events(raw, "阿伟")
    assert evs[0]["to"] == "局长"
    assert "to" not in evs[1], "乱写的去向按默认(桌上)处理"
