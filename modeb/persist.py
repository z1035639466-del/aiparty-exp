"""局中断点续局 v0:每拍快照 + --resume 恢复。

黑板架构的本意在这里兑现:一局的权威事实全在 GameState(账本)+ Engine 关键状态
(埋点计数/事件队列/荷官回执),把它们原子落盘,进程死了也能从盘上重建整局。
LLM 主持的对话 history **不持久化**——恢复后主持凭 digest + 荷官回执重新入场,
digest 是权威,这正是黑板设计要的:主持是插座不是居民,换一个上来照样接得住。

落盘姿势:先写临时文件再 os.replace 原子改名——半截快照永远不会被当成完整快照读到。
恢复裁定(遵仓内既有口径):
· 计时器是绝对 epoch,重启后一律作废清零,向事件队列注入 resume_note,主持自己圆场;
· 进行中的快枪手对决直接作废(duel=None),同样注记;
· episode 以追加模式续写原文件(审计线不断);
· 收局(finished)的快照不误恢复(落盘即清理,恢复入口再兜一道)。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from .state import GameState, SkillGrant

SNAPSHOT_V = 1
RESUME_NOTE = {"type": "resume_note", "note": "服务重启,计时器已清"}
DUEL_VOID_NOTE = {"type": "resume_note", "note": "服务重启,进行中的快枪手对决已作废"}


def state_path_for(episode_path: Path) -> Path:
    """快照与 episode 同目录同 stamp:sim_<stamp>.jsonl → sim_<stamp>.state.json。"""
    return episode_path.with_name(episode_path.stem + ".state.json")


def session_cfg(session) -> dict:
    """开局 cfg 一并存盘:恢复时按原 cfg 重建驱动/桌友(llm 主持无需 history)。"""
    return {
        "players": list(session.state.players),
        "wildness": session.state.wildness_cap,
        "minutes": session.state.time_budget_min,
        "objects": list(session.state.scene_objects),
        "driver": session.driver_kind,
        "score_style": session.state.score_style,
        "host_perception": session.state.host_perception,
        "playlist": list(session.state.playlist),
        "occasion": session.state.occasion,
        "scene_brief": session.state.scene_brief,
        "provider": session.provider,
        "host_model": session.host_model,
        "seat_model": session.seat_model,
        "bots": dict(session.bots_cfg),
        "autoplay": session.autoplay,
        "autoplay_interval_s": session.autoplay_interval_s,
    }


def build_snapshot(session) -> dict:
    """一拍后的完整快照:GameState 全量 + Engine 关键状态 + 收件箱 + 近期回合。"""
    s = session.state
    e = session.engine
    return {
        "v": SNAPSHOT_V,
        "finished": s.finished,
        "episode_path": str(session.episode_path),
        "cfg": session_cfg(session),
        "state": asdict(s),  # dataclass 递归成字典,grants(SkillGrant)一并展平
        "engine": {
            "marks": dict(e.marks),
            "event_queue": list(e.event_queue),
            "last_results": list(e._last_results),
            "host_error_streak": e.host_error_streak,
            "last_host_error": e.last_host_error,
        },
        # 收件箱是私件的落地内容(私件挂账 private_out 在 state 里,内容在这);两者都要续
        "inbox": {k: list(v) for k, v in session.inbox.items()},
        "recent": session.recent[-24:],  # 断点续看:恢复后驾驶舱/手机还能翻到刚才几拍
    }


def write_snapshot(session) -> None:
    """原子写:先写临时文件,再 os.replace 改名(同盘原子,半截文件读不到)。"""
    path = session.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(build_snapshot(session), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def clear_snapshot(session) -> None:
    """收局即清理快照:收完的局不该被 --resume 误当活局捞起来。"""
    try:
        session.state_path.unlink()
    except (FileNotFoundError, OSError):
        pass


def load_snapshot(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def restore_state(d: dict) -> GameState:
    """字典 → GameState:grants 单独还原成 SkillGrant,其余字段直接回填。"""
    fields = dict(d)
    grants = fields.pop("grants", [])
    gs = GameState(**fields)
    gs.grants = [SkillGrant(**g) for g in grants]
    return gs


def restore_session(snap: dict, out_dir: Path, join_base: str = ""):
    """从快照重建一整局 Session(episode 追加续写,计时器清零,对决作废)。"""
    from .simulator import Session  # 延迟导入,避开模块级循环依赖

    if snap.get("finished"):
        raise ValueError("该快照已收局(finished),不恢复")
    cfg = snap["cfg"]
    gs = restore_state(snap["state"])

    # —— 计时器是绝对 epoch,重启后全部清零 —— #
    gs.timers = []
    notes = []
    if gs.duel is not None:  # 进行中的快枪手对决直接作废
        gs.duel = None
        notes.append(dict(DUEL_VOID_NOTE))
    # open_ask 的 deadline 也是绝对 epoch:清空 deadline 退回「等第一个应声」态,
    # 免得恢复后凭一个陈旧时刻立刻误判超时结算。
    if gs.open_ask is not None:
        gs.open_ask["deadline"] = None
    notes.append(dict(RESUME_NOTE))  # 主持看到会自己圆场,反虚构不受影响

    episode_path = Path(snap["episode_path"])
    session = Session(
        players=cfg["players"], minutes=cfg["minutes"], wildness=cfg["wildness"],
        objects=cfg.get("objects", []), driver_kind=cfg.get("driver", "manual"),
        out_dir=out_dir, bots=cfg.get("bots") or {},
        provider=cfg.get("provider", "anthropic"),
        host_model=cfg.get("host_model", "sonnet"),
        seat_model=cfg.get("seat_model", "sonnet"),
        score_style=cfg.get("score_style", "自动"),
        host_perception=cfg.get("host_perception", "转写"),
        playlist=cfg.get("playlist") or [],
        occasion=cfg.get("occasion", ""), scene_brief=cfg.get("scene_brief", ""),
        state=gs, episode_path=episode_path, episode_mode="a",
    )

    # —— Engine 关键状态回填 —— #
    eng = snap.get("engine", {})
    session.engine.marks.update(eng.get("marks", {}))
    session.engine.event_queue = list(eng.get("event_queue", [])) + notes
    session.engine._last_results = list(eng.get("last_results", []))
    session.engine.host_error_streak = eng.get("host_error_streak", 0)
    session.engine.last_host_error = eng.get("last_host_error", "")

    # —— 收件箱 / 近期回合续接 —— #
    for k, v in snap.get("inbox", {}).items():
        if k in session.inbox:
            session.inbox[k] = list(v)
    session.recent = list(snap.get("recent", []))

    session.autoplay = bool(cfg.get("autoplay"))
    session.autoplay_interval_s = float(cfg.get("autoplay_interval_s", 1.0))
    session.join_base = join_base
    return session
