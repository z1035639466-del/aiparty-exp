"""复盘统计脚本:合成一局已知答案的流水,数字必须对得上。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.episode_report import analyze, render  # noqa: E402


def _line(turn, events=(), tools=(), results=()):
    return {"turn": turn, "digest": {"scores": {"甲": 0, "乙": 0}},
            "events_in": list(events), "text": "x",
            "tool_use": list(tools), "results": list(results)}


def test_report_numbers(tmp_path):
    ep = tmp_path / "sim_test.jsonl"
    rows = [
        _line(0,
              events=[{"type": "laugh", "player": "甲", "t_ms": 0},
                      {"type": "laugh", "player": "乙", "t_ms": 1000}],
              tools=[{"name": "draw_atom", "input": {}}],
              results=[{"tool": "draw_atom", "ok": True, "result": {"atom": {}}}]),
        _line(1,
              events=[{"type": "forfeit", "player": "乙", "t_ms": 120000},
                      {"type": "ask_result", "silent": ["甲"], "tally": {}}],
              tools=[{"name": "music.play", "input": {"track": "晴天"}},
                     {"name": "show", "input": {}}],
              results=[{"tool": "music.play", "ok": True, "result": {"playing": "晴天 - 周杰伦"}},
                       {"tool": "show", "ok": False, "clamped": "x"}]),
        {"turn": 2, "host_silent": True, "host_error": "429"},
        {"episode_summary": True, "marks": {}},
    ]
    ep.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    st = analyze(ep)
    assert st["turns"] == 2 and st["host_silent_beats"] == 1
    assert st["laugh_total"] == 2 and st["laugh_top2_share"] == 1.0
    assert st["signals"] == {"forfeit": 1}
    assert st["draw_atom"] == {"calls": 1, "ok": 1, "per_turn": 0.5}
    assert st["clamps"] == 1
    assert st["music_plays"] == ["晴天 - 周杰伦"]
    assert st["asks"] == {"count": 1, "silent_hits": 1}
    assert st["duration_min"] == 2.0
    assert st["speak_rate"]["甲"] == 0.5 and st["speak_rate"]["乙"] == 1.0

    page = render(st)
    assert "2 拍" in page and "晴天" in page and "0.5 条每拍" in page
