"""额头牌状态化:藏信息是长在人身上的道具,不是私件短信(房主裁定 2026-07-23)。

此前 额头 可见性只投私件流水(👀 文本行),App 端呈现成了聊天;正解是"点这个
玩家看他的牌"。服务端把牌挂进 state.foreheads,player_view 给出别人的牌、
自己那张永远缺席——可见性反转在服务端成立。私件 👀 行保留(向后兼容)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _session(tmp_path, names=("甲", "乙", "丙")):
    from modeb.simulator import Session
    return Session(list(names), 30, 6, [], "manual", tmp_path)


def test_forehead_is_a_prop_on_the_player(tmp_path):
    s = _session(tmp_path)
    call = {"name": "show", "input": {"content": "大象", "visibility": "额头", "player": "甲"}}
    res = s.engine.tools.execute(call)
    assert res["ok"], res
    s.route_private({"tool_use": [call], "results": [res]})
    # 乙丙的视图:点甲能看到牌
    assert s.player_view("乙")["foreheads"] == {"甲": "大象"}
    assert s.player_view("丙")["foreheads"] == {"甲": "大象"}
    # 甲自己的视图:自己那张永远缺席,整份 JSON 无牌面内容
    v_jia = s.player_view("甲")
    assert v_jia["foreheads"] == {}
    assert "大象" not in json.dumps(v_jia, ensure_ascii=False)
    # 向后兼容:👀 私件行仍投其余人
    assert any("额头·甲" in x for x in s.player_view("乙")["inbox"])
