"""超句截断不许静默(真机病历 2026-07-24)。

桌上收到"碰拳伸手指,对面复述,说错的接惩罚"这种没头没尾的电报文——三句硬截断
把玩法讲解的后半段吞了,警告只进审计日志,局长自己毫不知情,下一拍也不会补。
修法(修法优先级裁定第 1 级,引擎守卫):被剪掉的原文下一拍以 host_text_trimmed
随 driver 信道原样奉还(与荷官回执同姿势,仅主持可见),补救权交回它手里;
玩法步骤的正确容身处是 show 玩法卡(content 可多行),回执注记里指路。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.engine import Engine  # noqa: E402
from modeb.state import GameState  # noqa: E402


class _Capture:
    """记录每拍收到的 events,按脚本轮流出台词。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.seen: list[list] = []

    def decide(self, digest, events):
        self.seen.append(events)
        text = self.scripts.pop(0) if self.scripts else ""
        return {"text": text, "tool_use": []}


def _engine(tmp_path, driver):
    st = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    return Engine(st, driver, tmp_path / "ep.jsonl")


def test_trimmed_text_returned_next_beat(tmp_path):
    long = "第一句。第二句。第三句。玩法关键步骤A。玩法关键步骤B。"
    drv = _Capture([long, "补上了。", "第三拍。"])
    e = _engine(tmp_path, drv)
    e.turn()  # 超句拍:第四句起被截
    e.turn()  # 下一拍:截断回执奉还被剪原文
    trims = [x for x in drv.seen[1] if x.get("type") == "host_text_trimmed"]
    assert len(trims) == 1, "截断回执该在下一拍送达主持"
    assert "玩法关键步骤A。玩法关键步骤B。" == trims[0]["dropped"], "被剪原文原样奉还"
    assert "玩法卡" in trims[0]["note"], "注记要指路 show 玩法卡"
    e.turn()  # 回执单发不循环:第三拍不再出现
    assert not any(x.get("type") == "host_text_trimmed" for x in drv.seen[2])


def test_no_trim_no_receipt(tmp_path):
    drv = _Capture(["一句话收口。", "第二拍。"])
    e = _engine(tmp_path, drv)
    e.turn()
    e.turn()
    assert not any(x.get("type") == "host_text_trimmed" for x in drv.seen[1]), \
        "没超句就没有截断回执,不注水"


def test_trim_receipt_not_in_player_view_types(tmp_path):
    """截断回执只走 driver 信道:不进 event_queue,玩家 live 白名单天然拿不到。"""
    long = "一。二。三。四。"
    drv = _Capture([long, ""])
    e = _engine(tmp_path, drv)
    e.turn()
    assert not any(x.get("type") == "host_text_trimmed" for x in e.event_queue), \
        "回执不该走公共事件队列(那会泄进玩家面/白白唤醒主持)"
