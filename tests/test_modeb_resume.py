"""局中断点续局 v0 验收:进程死掉/重启后,正在进行的一局能从盘上重建继续玩。

造一局(manual 驱动)推几拍 + 私发 + 加分,直接从 sim_<stamp>.state.json 快照
重建新 Session/引擎,断言账本原样、可继续、episode 续写不覆盖。
恢复裁定同时验:计时器清零、resume_note 入队、进行中的对决作废、收局快照不误恢复。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb import persist  # noqa: E402
from modeb.simulator import Session  # noqa: E402


def _play(sess, body):
    with sess.lock:
        return sess.run_turn(body)


def _fresh_game(tmp_path):
    """两人 manual 局,推四拍:开轮 → 私发额头/自己看 → 加分 → 抽原子。"""
    sess = Session(players=["甲", "乙"], minutes=30, wildness=6,
                   objects=["瓶子", "冰块"], driver_kind="manual", out_dir=tmp_path)
    _play(sess, {"text": "开局", "tool_use": [{"name": "state.next_round", "input": {}}]})
    _play(sess, {"text": "发牌", "tool_use": [
        {"name": "show", "input": {"content": "秘密任务:学猫叫三声",
                                   "visibility": "自己看", "player": "乙"}}]})
    _play(sess, {"text": "加分", "tool_use": [
        {"name": "state.add_score", "input": {"player": "甲", "delta": 2, "reason": "过关"}}]})
    _play(sess, {"text": "抽一张", "tool_use": [{"name": "draw_atom", "input": {"野度": 6}}]})
    return sess


def test_snapshot_written_each_turn(tmp_path):
    """每拍后快照落盘(原子写),且与 episode 同目录同 stamp。"""
    sess = _fresh_game(tmp_path)
    assert sess.state_path.exists(), "每拍后应有 sim_<stamp>.state.json 快照"
    assert sess.state_path.parent == sess.episode_path.parent
    assert sess.state_path.name == sess.episode_path.stem + ".state.json"
    snap = persist.load_snapshot(sess.state_path)
    assert snap["finished"] is False and snap["v"] == persist.SNAPSHOT_V


def test_restore_preserves_ledger_and_continues(tmp_path):
    """从快照重建:分数/回合数/收件箱/私件挂账/atoms_used 原样,且可继续 turn。"""
    sess = _fresh_game(tmp_path)
    before = {
        "scores": dict(sess.state.scores),
        "round": sess.state.round_no,
        "atoms_used": list(sess.state.atoms_used),
        "private_out": list(sess.state.private_out),
        "inbox_乙": list(sess.inbox["乙"]),
        "turns": sess.engine.marks["turns"],
    }
    # 前置校验:这四拍确实动了账本,否则"原样"断言毫无意义(对照)
    assert before["scores"]["甲"] == 2 and before["round"] == 1
    assert before["atoms_used"] and any("学猫叫" in x for x in before["inbox_乙"])
    assert before["private_out"] == [{"holder": "乙", "kind": "自己看"}]

    snap = persist.load_snapshot(sess.state_path)
    sess2 = persist.restore_session(snap, tmp_path)

    assert sess2.state.scores == before["scores"], "分数应原样"
    assert sess2.state.round_no == before["round"], "回合数应原样"
    assert sess2.state.atoms_used == before["atoms_used"], "atoms_used 应原样"
    assert sess2.state.private_out == before["private_out"], "私件挂账应原样"
    assert sess2.inbox["乙"] == before["inbox_乙"], "收件箱应原样(私发原文)"
    assert sess2.engine.marks["turns"] == before["turns"], "已跑拍数应原样"

    # resume_note 已注入事件队列——主持醒来会自己圆场
    assert any(e.get("type") == "resume_note" for e in sess2.engine.event_queue)

    # 可继续 turn:再加一分,账本在恢复态上正确累加
    _play(sess2, {"text": "再加", "tool_use": [
        {"name": "state.add_score", "input": {"player": "乙", "delta": 1, "reason": "接力"}}]})
    assert sess2.state.scores["乙"] == 1
    assert sess2.engine.marks["turns"] == before["turns"] + 1


def test_episode_appended_not_overwritten(tmp_path):
    """恢复后 episode 以追加模式续写原文件:历史行不丢、不被覆盖。"""
    sess = _fresh_game(tmp_path)
    original = sess.episode_path.read_text(encoding="utf-8").splitlines()
    assert len(original) >= 4

    snap = persist.load_snapshot(sess.state_path)
    sess2 = persist.restore_session(snap, tmp_path)
    assert sess2.episode_path == sess.episode_path, "应续写同一个 episode 文件"

    _play(sess2, {"text": "续局一拍", "tool_use": []})
    after = sess2.episode_path.read_text(encoding="utf-8").splitlines()
    assert after[:len(original)] == original, "历史流水必须原样保留(未被覆盖)"
    assert len(after) > len(original), "续局的拍应追加在后面"
    assert json.loads(after[-1]).get("text") == "续局一拍"


def test_resume_clears_timers_and_voids_duel(tmp_path):
    """计时器是绝对 epoch,恢复时全部清零;进行中的快枪手对决直接作废。"""
    sess = Session(players=["甲", "乙", "丙"], minutes=30, wildness=6,
                   objects=["杯子"], driver_kind="manual", out_dir=tmp_path)
    _play(sess, {"text": "计时+对决", "tool_use": [
        {"name": "timer", "input": {"seconds": 300, "label": "长挑战"}},
        {"name": "duel.start", "input": {"players": ["甲", "乙"]}}]})
    assert sess.state.timers and sess.state.duel is not None

    snap = persist.load_snapshot(sess.state_path)
    sess2 = persist.restore_session(snap, tmp_path)
    assert sess2.state.timers == [], "计时器恢复时应全部清零"
    assert sess2.state.duel is None, "进行中的对决恢复时应作废"
    notes = [e for e in sess2.engine.event_queue if e.get("type") == "resume_note"]
    assert any("对决" in e["note"] for e in notes), "对决作废要有注记"
    assert any("计时器" in e["note"] for e in notes), "计时器清零要有注记"


def test_finished_snapshot_is_cleaned_and_not_restorable(tmp_path):
    """收局的快照落盘即清理;万一残留,恢复入口也拒绝(不误恢复已收的局)。"""
    sess = _fresh_game(tmp_path)
    state_path = sess.state_path
    _play(sess, {"text": "收!", "tool_use": [{"name": "state.finish", "input": {}}]})
    assert not state_path.exists(), "收局后快照应被清理"

    # 兜底:即便手上还攥着一份收局快照,恢复入口也必须拒绝
    finished_snap = {"finished": True, "cfg": {}, "state": {}, "engine": {}}
    with pytest.raises(ValueError):
        persist.restore_session(finished_snap, tmp_path)


def test_grants_survive_roundtrip(tmp_path):
    """技能授予(SkillGrant)要能穿越快照:恢复后仍是可发动的权力件,不退化成裸字典。"""
    from modeb.state import GameState, SkillGrant
    from modeb.persist import restore_state
    gs = GameState(players=["甲", "乙"], wildness_cap=6, time_budget_min=30)
    gs.grants.append(SkillGrant(prop="免死金牌", holder="甲", bound_object="瓶子",
                                uses_left=2, ritual="举牌喊免"))
    from dataclasses import asdict
    gs2 = restore_state(asdict(gs))
    assert len(gs2.grants) == 1 and isinstance(gs2.grants[0], SkillGrant)
    assert gs2.grants[0].prop == "免死金牌" and gs2.grants[0].uses_left == 2
