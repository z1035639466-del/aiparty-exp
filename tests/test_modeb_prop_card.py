"""牌卡道具全链路验收:私发内容全面道具化(房主裁定 2026-07-23)。

卧底词/情侣密令/毒杯号不是"私信文本",是有类型有生命周期的**牌**。show(自己看)
自由文本口是文字流游戏的最后后门,用类型化的 prop.card 取代。
钉死:发牌本人视图有内容、旁人只见类型、公开面无内容;批量+单发;钳制(超长/
号码卡非数字/不在座);reveal 后内容进公开与 table_cards;digest 无内容;
荷官回执(仅局长)拿得到内容。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.state import GameState  # noqa: E402
from modeb.tools import ToolExecutor  # noqa: E402


def _ex(names=("甲", "乙", "丙")) -> ToolExecutor:
    return ToolExecutor(GameState(players=list(names), wildness_cap=6,
                                  time_budget_min=30), rng_seed=7)


def _session(tmp_path, names=("甲", "乙", "丙")):
    from modeb.simulator import Session
    return Session(list(names), 30, 6, [], "manual", tmp_path)


class _Recorder:
    """记录 engine 送进 driver 的 (digest, events)——验证荷官回执到没到主持手里。"""

    def __init__(self) -> None:
        self.seen: list[tuple] = []

    def decide(self, digest: dict, events: list[dict]) -> dict:
        self.seen.append((digest, events))
        return {"text": "", "tool_use": []}


class _DealOnce:
    """首拍发一次牌(把结果写进 engine._last_results),之后空拍。"""

    def __init__(self, call: dict) -> None:
        self.call = call
        self.done = False

    def decide(self, digest: dict, events: list[dict]) -> dict:
        if self.done:
            return {"text": "", "tool_use": []}
        self.done = True
        return {"text": "发牌了", "tool_use": [self.call]}


# —— ① 发牌:工具面钳制(kind 合法、内容长度、号码卡数字、在座)——

def test_deal_card_clamps():
    ex = _ex()
    # kind 不合法
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "鬼卡", "content": "x", "to": "甲"}})["ok"]
    # 空内容
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "词卡", "content": "  ", "to": "甲"}})["ok"]
    # 词卡超 12 字
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "词卡", "content": "字" * 13, "to": "甲"}})["ok"]
    assert ex.execute({"name": "prop.card",
                       "input": {"kind": "词卡", "content": "字" * 12, "to": "甲"}})["ok"]
    # 密令卡超 60 字
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "密令卡", "content": "令" * 61, "to": "乙"}})["ok"]
    # 号码卡非数字被钳,纯数字放行
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "号码卡", "content": "3号", "to": "丙"}})["ok"]
    assert ex.execute({"name": "prop.card",
                       "input": {"kind": "号码卡", "content": "3", "to": "丙"}})["ok"]
    # 目标不在座整单驳回
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "词卡", "content": "词", "players": ["甲", "路人"]}})["ok"]
    # 无 to 无 players 驳回
    assert not ex.execute({"name": "prop.card",
                           "input": {"kind": "词卡", "content": "词"}})["ok"]


# —— ② 发牌:批量(平民词)+单发(卧底词),一人可持多张 ——

def test_deal_batch_and_single():
    ex = _ex()
    ex.execute({"name": "prop.card",
                "input": {"kind": "词卡", "content": "苹果", "players": ["甲", "乙"]}})
    ex.execute({"name": "prop.card", "input": {"kind": "词卡", "content": "梨", "to": "丙"}})
    # 再给甲发一张号码卡:一人可持多张
    ex.execute({"name": "prop.card", "input": {"kind": "号码卡", "content": "7", "to": "甲"}})
    assert [c["content"] for c in ex.state.cards["甲"]] == ["苹果", "7"]
    assert ex.state.cards["乙"][0]["content"] == "苹果"
    assert ex.state.cards["丙"][0] == {"kind": "词卡", "content": "梨",
                                       "dealt_turn": 0, "status": "held"}
    for cs in ex.state.cards.values():
        assert all(c["status"] == "held" for c in cs)


# —— ③ 本人视图有内容、旁人只见类型、公开面无内容 ——

def test_view_isolation(tmp_path):
    s = _session(tmp_path)
    call = {"name": "prop.card",
            "input": {"kind": "词卡", "content": "卧底", "to": "甲"}}
    res = s.engine.tools.execute(call)
    assert res["ok"], res
    red = s.route_private({"tool_use": [call], "results": [res]})
    # 本人视图:my_cards 带内容(常驻牌区)
    va = s.player_view("甲")
    assert va["my_cards"] == [{"kind": "词卡", "content": "卧底", "status": "held"}]
    # 旁人视图:table_cards 只见类型+状态,无内容;my_cards 空
    vb = s.player_view("乙")
    assert vb["my_cards"] == []
    tc = {c["player"]: c for c in vb["table_cards"]}
    assert tc["甲"] == {"player": "甲", "kind": "词卡", "status": "held"}
    assert "卧底" not in json.dumps(vb, ensure_ascii=False), "旁人视图漏了牌面"
    # 牌面进本人收件箱(🎴 前缀,区别于 🔒/👀)
    assert any(m.startswith("🎴") and "卧底" in m for m in s.inbox["甲"])
    assert s.inbox["乙"] == []
    # 公开回合行(遮蔽版 red)不含牌面
    assert "卧底" not in json.dumps(red, ensure_ascii=False), "公开面漏了牌面"
    # digest 挂账:谁持什么类型什么状态,无内容
    dc = s.state.digest(30)["cards"]
    assert dc == [{"player": "甲", "kind": "词卡", "status": "held"}]
    assert "卧底" not in json.dumps(dc, ensure_ascii=False)


# —— ④ 批量私发:每人各收各的牌面,互不可见,公开面无内容 ——

def test_batch_deal_inbox_and_mask(tmp_path):
    s = _session(tmp_path)
    call = {"name": "prop.card",
            "input": {"kind": "词卡", "content": "西瓜", "players": ["甲", "乙", "丙"]}}
    res = s.engine.tools.execute(call)
    red = s.route_private({"tool_use": [call], "results": [res]})
    for p in ("甲", "乙", "丙"):
        assert any("西瓜" in m for m in s.inbox[p])
    # 遮蔽后公开面(含 tool_use.input)不含牌面
    assert "西瓜" not in json.dumps(red, ensure_ascii=False)


# —— ⑤ reveal:内容进公开面(全场公开 display)+ table_cards ——

def test_reveal_goes_public(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.card",
                            "input": {"kind": "号码卡", "content": "5", "to": "甲"}})
    call = {"name": "prop.card_reveal", "input": {"player": "甲"}}
    res = s.engine.tools.execute(call)
    assert res["ok"], res
    assert res["result"]["visibility"] == "全场公开"
    assert "5" in res["result"]["display"]
    s.recent.append(s.route_private({"turn": 1, "tool_use": [call], "results": [res]}))
    # 状态翻到 revealed
    assert s.state.cards["甲"][0]["status"] == "revealed"
    # 旁人视图:table_cards 现在给出内容(揭晓=公开信息)
    vb = s.player_view("乙")
    tc = {c["player"]: c for c in vb["table_cards"]}
    assert tc["甲"] == {"player": "甲", "kind": "号码卡", "status": "revealed", "content": "5"}
    # 公开回合行 shown 里出现揭晓文案(全场公开 display 不遮)
    assert any("5" in (r or "") for line in vb["recent"] for r in line["shown"])


# —— ⑥ card_use:标 used,牌面不进公开面 ——

def test_card_use_marks_status(tmp_path):
    s = _session(tmp_path)
    s.engine.tools.execute({"name": "prop.card",
                            "input": {"kind": "密令卡", "content": "对视三秒", "to": "甲"}})
    res = s.engine.tools.execute({"name": "prop.card_use", "input": {"player": "甲"}})
    assert res["ok"], res
    assert s.state.cards["甲"][0]["status"] == "used"
    # used 牌面仍不进公开面:旁人 table_cards 无内容
    vb = s.player_view("乙")
    tc = {c["player"]: c for c in vb["table_cards"]}
    assert tc["甲"] == {"player": "甲", "kind": "密令卡", "status": "used"}
    assert "对视三秒" not in json.dumps(vb, ensure_ascii=False)
    # 名下无可用 held 牌了:再 use 驳回
    assert not s.engine.tools.execute({"name": "prop.card_use", "input": {"player": "甲"}})["ok"]


# —— ⑦ card_cancel:收牌(全收/按型收)——

def test_card_cancel():
    ex = _ex()
    ex.execute({"name": "prop.card", "input": {"kind": "词卡", "content": "A", "to": "甲"}})
    ex.execute({"name": "prop.card", "input": {"kind": "号码卡", "content": "9", "to": "甲"}})
    # 按型收:只收号码卡,词卡留下
    ex.execute({"name": "prop.card_cancel", "input": {"player": "甲", "kind": "号码卡"}})
    assert [c["kind"] for c in ex.state.cards["甲"]] == ["词卡"]
    # 全收:甲从 cards 里消失
    ex.execute({"name": "prop.card_cancel", "input": {"player": "甲"}})
    assert "甲" not in ex.state.cards


# —— ⑧ 荷官回执:牌面走 driver 专属信道(仅主持),不进公开面 ——

def test_dealer_receipt_reaches_host_not_public(tmp_path):
    s = _session(tmp_path)
    # 首拍:主持发一张卧底词卡
    s.engine.driver = _DealOnce({"name": "prop.card",
                                 "input": {"kind": "词卡", "content": "间谍", "to": "甲"}})
    line = s.run_turn()
    # 公开回合行不含牌面
    assert "间谍" not in json.dumps(line, ensure_ascii=False), "公开面漏了牌面"
    # 下一拍:荷官回执把上一拍发的牌原文回给主持(仅主持可见)
    rec = _Recorder()
    s.engine.driver = rec
    s.engine.turn()
    _digest, events = rec.seen[-1]
    receipts = next((e for e in events if e.get("type") == "tool_receipts"), None)
    assert receipts is not None, "荷官回执没送到主持手里"
    assert "间谍" in json.dumps(receipts, ensure_ascii=False), "荷官回执里拿不到牌面"
    # digest(公共面)只有类型+状态,无牌面
    assert "间谍" not in json.dumps(_digest, ensure_ascii=False)
    # 旁人视图仍旧拿不到牌面
    assert "间谍" not in json.dumps(s.player_view("乙"), ensure_ascii=False)
