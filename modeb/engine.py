"""M1 回合循环:事件聚合 → 驱动器决策 → 钳制执行 → episode 落盘。

协议 v0 §四:规则内事件本地零 API;决策点聚合一次驱动器回合。
埋点四信号(裁定纪要 §③,自第一行代码起带上):laugh / skip / duration / replay。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol

from .state import GameState
from .tools import ToolExecutor

MAX_TOOLS_PER_TURN = 2
MAX_SENTENCES = 3
HOST_COOLDOWN_S = 8  # 主持调用失败后的冷却拍:期间 turn_ready=False,事件在队列里等


class Driver(Protocol):
    """决策器接口:活局长(LLM)与脚本驱动同签名——插座,不是居民。"""

    def decide(self, digest: dict, events: list[dict]) -> dict: ...


class Engine:
    def __init__(self, state: GameState, driver: Driver, episode_path: Path, rng_seed: int = 0,
                 episode_mode: str = "w") -> None:
        self.state = state
        self.driver = driver
        self.tools = ToolExecutor(state, rng_seed)
        self.episode_path = episode_path
        self.event_queue: list[dict] = []
        self.marks = {"laugh_events": 0, "skips": 0, "forfeits": 0, "turns": 0,
                      "host_errors": 0}
        self.host_error_streak = 0      # 连续沉默拍;成功一拍归零,≥5 台面亮红牌
        self.last_host_error = ""
        self.cooldown_until = 0.0       # 失败后的冷却:免得自动循环每秒撞一次死 API
        self._wake_retry = False        # 唤醒券补发标记:timer_expired 被空拍吞掉只补一次
        self._closer_retry = False      # 束手拍提醒标记:no_closer 单发不循环
        # 荷官回执:主持上一拍工具的真实结果,只走 driver 专用信道回给它本人。
        # 遮蔽按观看者定,不按出口定——玩家面/驾驶舱照旧遮,发牌人看自己发的牌。
        self._last_results: list[dict] = []
        self.last_host_ms = 0  # 上一拍主持调用耗时(慢要先量再修)
        # 没有冷场闹钟(房主撤,2026-07-20):时限的唯一机制是主持显式调 timer,
        # 没设时限=选择了开放式等待,一直等是正确行为("没回应就等,就这么简单")。
        # agent 桌冻死是 harness 座位掉线,归座位保活/产品在线心跳,不归引擎。
        self._t0 = time.time()
        episode_path.parent.mkdir(parents=True, exist_ok=True)
        # episode_mode="a":局中断点续局时以追加模式续写原文件,不覆盖既有流水
        #(裁定:episode 是审计线,恢复后接着写,历史一行都不能丢)。
        self._ep = episode_path.open(episode_mode, encoding="utf-8")

    # —— 玩家端/观察员事件入队(两回合之间聚合,不逐条打驱动器) ——
    def push_event(self, ev: dict) -> None:
        ev.setdefault("t_ms", int(time.time() * 1000))  # 竞速判定公平依据(快枪手等)
        if ev.get("type") == "laugh":
            self.marks["laugh_events"] += 1
        if ev.get("type") in ("pass", "optout"):  # 安全退出(零代价底线,罕用):立即生效
            self.marks["skips"] += 1
        if ev.get("type") == "forfeit":  # 认罚跳过(日常的「过」):正常游戏动作,按赌注结算
            self.marks["forfeits"] += 1
        duel = self.state.duel
        if (duel and ev.get("type") == "tap" and ev.get("player") in duel["players"]):
            # 快枪手抢拍:第一下 tap 立即定胜负——枪响前抢跑判负,枪响后先到者胜。
            # 对决中的 tap 优先归对决(先于 ask 吸收),被吸收不单独叫醒主持,
            # duel_result 会叫。
            self._resolve_duel_tap(ev)
            ev = dict(ev, _absorbed=True)
        elif self._is_answer_to_open_ask(ev):
            ask = self.state.open_ask
            # 应答归到这次问询名下,一人一票、后答覆盖先答。
            ask["answers"][ev["player"]] = ev.get("value") or ev.get("text") or "确认"
            if ask.get("mode") == "轮流":
                self._advance_round_ask(ask)  # 答完即轮下一位,不等窗口烧完
            elif ask["deadline"] is None:
                # 抢答:计时从「第一个人应声」才开始,给后到的人留窗口。
                # 没人应声就不计时——安静等着,不催。
                ask["deadline"] = time.time() + ask["window"]
                self.state.timers.append(ask["deadline"])
            ev = dict(ev, _absorbed=True)  # 已被问询吸收,不单独叫醒主持
        self.event_queue.append(ev)

    def _resolve_duel_tap(self, ev: dict) -> None:
        """快枪手判定:毫秒级、单发定胜负,公平由系统而非任何人的眼睛保证。"""
        duel = self.state.duel
        p = ev["player"]
        other = next(x for x in duel["players"] if x != p)
        draw_ms = int(duel["draw_at"] * 1000)
        lead = ev["t_ms"] - draw_ms
        duel["taps"][p] = ev["t_ms"]
        self.state.duel = None
        if lead < 0:
            result = {"winner": other, "loser": p,
                      "reason": f"{p} 抢跑(枪响前 {-lead}ms)判负"}
        else:
            result = {"winner": p, "loser": other,
                      "reason": f"{p} 枪响后 {lead}ms 先拔"}
        self.event_queue.append({"type": "duel_result", **result,
                                 "vs": list(duel["players"]), "taps": dict(duel["taps"])})

    def _is_answer_to_open_ask(self, ev: dict) -> bool:
        """哪些事件算这次问询的应答——不设门槛就会被无关闲聊截胡。

        实测(4人桌 turn 57):主持点名问毛毛,阿哲一句发给老K的「糯米丸子」闲聊
        被吸收成唯一答案并判 winner,毛毛的真答案在窗口关掉后才到、直接作废。
        玩家原话:「我随便插一句话就能截胡任何一个 ask」。三道门槛:
        · 点名问某人时,只认那个人的应答(问全场则不限人);
        · say 只认 to=局长 的定向发言,桌上互说是气氛不是答案;
        · vote / tap 是刻意的应答动作,一律认。
        """
        ask = self.state.open_ask
        if not ask or not ev.get("player"):
            return False
        if ask["deadline"] is not None and time.time() > ask["deadline"]:
            return False
        asked = ask.get("asked")
        if asked and asked not in ("全场", "all") and ev["player"] != asked:
            return False
        if ev.get("type") == "say":
            return ev.get("to") == "局长"
        return ev.get("type") in ("vote", "tap")

    def _advance_round_ask(self, ask: dict) -> None:
        """轮流问询推进:下一位开新窗;没有下一位就把 deadline 拨到现在,待收。"""
        if ask["queue"]:
            ask["asked"] = ask["queue"].pop(0)
            ask["deadline"] = time.time() + ask["window"]
        else:
            ask["deadline"] = time.time() - 0.001
        self.state.timers.append(ask["deadline"])

    def _close_ask(self) -> dict | None:
        """窗口到点:按多数认,一票也认,平票取先到,没人答就明说没人答。"""
        ask = self.state.open_ask
        # deadline 为 None = 还没人应声。安静等着,不催、不结算、不叫醒主持。
        if not ask or ask["deadline"] is None or time.time() < ask["deadline"]:
            return None
        if ask.get("mode") == "轮流" and (ask["queue"] or
                                          ask["asked"] not in ask["answers"]):
            # 当前这位超时没答:记一笔(挤掉≠沉默的另一面:轮到了没接),轮下一位
            if ask["asked"] not in ask["answers"] and ask["queue"]:
                self._advance_round_ask(ask)
                return None
            if ask["asked"] not in ask["answers"] and not ask["queue"]:
                pass  # 最后一位也超时:落到下面正常收
        self.state.open_ask = None
        tally: dict[str, int] = {}
        for v in ask["answers"].values():
            tally[v] = tally.get(v, 0) + 1
        winner = max(tally, key=lambda k: tally[k])
        # 被问未答名单:主持曾把"被窗口挤掉"读成"安静得可疑"并据此下判——
        # 它不是不想管,是数据里没有这个信息。挤掉≠沉默,必须可分辨。
        if ask.get("mode") == "轮流":
            audience = ask.get("order_all", [])
        elif ask.get("asked") in (None, "全场"):
            audience = self.state.players
        else:
            audience = [ask["asked"]]
        silent = [p for p in audience if p not in ask["answers"]]
        return {"type": "ask_result", "prompt": ask["prompt"], "tally": tally,
                "winner": winner, "answers": dict(ask["answers"]), "silent": silent,
                "note": "按多数认,一票也认;silent=被问未答(可能是没赶上窗口,不是故意不说)"}

    def time_left_min(self) -> float:
        return self.state.time_budget_min - (time.time() - self._t0) / 60.0

    def _perceive(self, events: list[dict]) -> list[dict]:
        """按感知档裁剪送给主持的事件——决定这局在多接近真机的条件下被测。

        发言分两路(M2 实测改进项:现实里天然分开,只有 agent 桌混着):
        - to=局长:定向频道(真机=打字/按住说话),任何档全文送达;
        - to=桌上(默认):自由交谈。按钮档降级成「有人说话」,内容听不见。

        按钮档就是真机基线(房主裁定 2026-07-20):录音/监听类感知一概不做,
        主持的感官 = 三信号按键 + 定向发言 + 计时器。转写档只是开发台的
        上帝视角仪器(agent 桌的 say 本来就是文字),测的是理想上限,非产品形态。
        """
        mode = getattr(self.state, "host_perception", "转写")
        if mode != "按钮":
            return events
        out = []
        for e in events:
            if e.get("type") == "say" and e.get("to") != "局长":
                out.append({"type": "say", "player": e.get("player"), "inaudible": True})
            else:
                out.append(e)
        return out

    def turn_ready(self) -> bool:
        """事件驱动心跳:开局首拍 / 有新事件 / 有计时器到点,才该叫醒主持。
        房主裁定(2026-07-18):没回应就等——桌上没动静不打扰,主持不必编进展。"""
        # 被问询吸收的应答不单独叫醒主持——等窗口收完一起给它,免得它半途插话。
        # 按钮档下桌上互说也不叫醒:聋主持面前的闲聊是背景噪音,为一句
        # 「有人说了你听不见的话」烧一个回合,只会逼它对着空气编话(反虚构)。
        deaf = getattr(self.state, "host_perception", "转写") == "按钮"

        def wakes(e: dict) -> bool:
            if e.get("_absorbed"):
                return False
            if deaf and e.get("type") == "say" and e.get("to") != "局长":
                return False
            return True

        if time.time() < self.cooldown_until:
            return False  # 主持刚断线:冷却期内不叫醒,事件都在队列里等着
        if self.marks["turns"] == 0 or any(wakes(e) for e in self.event_queue):
            return True
        now = time.time()
        return any(t <= now for t in self.state.timers)

    def log_meta(self, kind: str, payload: dict) -> None:
        """episode 里的非回合元信息行(设备绑定等)。与回合行同文件同流水——
        episode 是产品的用户数据资产,账号体系上线后按这些锚点回溯认领历史局。"""
        self._ep.write(json.dumps({"meta": kind, **payload}, ensure_ascii=False) + "\n")
        self._ep.flush()

    # —— 一个决策回合 ——
    def turn(self) -> dict:
        now = time.time()
        fired = [t for t in self.state.timers if t <= now]
        self.state.timers = [t for t in self.state.timers if t > now]
        digest = self.state.digest(self.time_left_min())
        events, self.event_queue = self.event_queue, []
        events += [{"type": "timer_expired"} for _ in fired]
        closed = self._close_ask()
        if closed:
            events.append(closed)
        # 现场的局长是半瞎半聋的:听不见桌上的自由交谈,只知道谁按了什么。
        # 模拟台默认给全文,会把主持的本桌化改造能力测得虚高——真机上它拿不到这些。
        upstream = self._perceive(events)
        if self._last_results:
            # 荷官回执:只进 driver 信道,不进 line/events_in/任何 HTTP 面——
            # 上一拍的私发原文、随机点数、钳制记录,发牌人有权看自己发的牌。
            upstream = [{"type": "tool_receipts",
                         "note": "你上一拍工具的真实回执(仅你可见,别念出来;被钳制的必须圆场)",
                         "results": self._last_results}] + upstream
        # 骰盅对账信道:玩家自己摇的点数只走这条私信道回给主持(与荷官回执同姿势,
        # 仅主持可见,不进 line/events_in/digest/别人的 view)。摇了盅的点数每拍照亮,
        # 一路留到开牌——开牌结算凭这条报点数,不用靠玩家自报。从 state.props 现取,
        # 随收盅/重发自然更新。
        cups = {p: pr["rolled"] for p, pr in self.state.props.items()
                if pr.get("rolled") is not None}
        if cups:
            ledger = {"type": "dice_cup_ledger",
                      "note": "各人骰盅已摇出的点数(玩家自己摇的,仅你对账可见;"
                              "别念点数,开牌结算才亮)",
                      "points": cups}
            # 开牌标随对账信道每拍照亮(challenge 事件只出现一拍,这条留到局长收盅):
            # 清算拍谁开的+被开叫价+真点数一屏齐,不用翻旧拍也不用问玩家。
            ch = next((pr for pr in self.state.props.values()
                       if pr.get("challenged_by")), None)
            if ch:
                ledger["challenge"] = {"challenged_by": ch["challenged_by"],
                                       "bid": ch.get("bid")}
            upstream = [ledger] + upstream
        t_decide = time.time()
        try:
            decision = self.driver.decide(digest, upstream)
        except Exception as e:
            # 主持沉默拍:错误不进游戏,只进台面。事件塞回队列头一个不丢
            # (否则玩家按的「完成」就此蒸发),冷却几拍后 turn_ready 自然重来。
            self.event_queue = events + self.event_queue
            self.marks["host_errors"] += 1
            self.host_error_streak += 1
            self.last_host_error = f"{type(e).__name__}: {e}"
            self.cooldown_until = time.time() + HOST_COOLDOWN_S
            line = {"turn": self.marks["turns"], "host_silent": True,
                    "host_error": self.last_host_error, "events_requeued": len(events)}
            self._ep.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._ep.flush()
            return line
        self.host_error_streak = 0
        self.last_host_ms = int((time.time() - t_decide) * 1000)  # 慢要先量再修
        text = decision.get("text", "")
        calls = decision.get("tool_use", [])[:MAX_TOOLS_PER_TURN]
        overflow = len(decision.get("tool_use", [])) - len(calls)
        sentence_count = sum(text.count(p) for p in "。!?!?") or (1 if text else 0)
        # 超句硬截断(真机节奏裁定 2026-07-24):warning 不改行为,主持一超再超。
        # 在第 MAX_SENTENCES 个句读符处切——说得短=出得快,桌上等不起长篇。
        if sentence_count > MAX_SENTENCES:
            seen = 0
            for i, ch in enumerate(text):
                if ch in "。!?!?":
                    seen += 1
                    if seen == MAX_SENTENCES:
                        text = text[:i + 1]
                        break
        scores_before = dict(self.state.scores)
        results = [self.tools.execute(c) for c in calls]
        line = {
            "turn": self.marks["turns"], "digest": digest, "events_in": events,
            "text": text, "tool_use": calls, "results": results,
            "ledger_diff": {p: self.state.scores[p] - scores_before[p]
                            for p in self.state.scores if self.state.scores[p] != scores_before[p]},
            "warnings": ([f"tool_use 超限截断 {overflow} 个"] if overflow > 0 else [])
                        + ([f"主持词 {sentence_count} 句超 {MAX_SENTENCES}"] if sentence_count > MAX_SENTENCES else []),
        }
        self._ep.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._ep.flush()  # 审计线逐行落地:进程中途死掉,流水不能跟着蒸发
        self.marks["turns"] += 1
        self._last_results = results  # 荷官回执:下一拍随 driver 信道回给主持本人
        # 唤醒不许浪费(真机死锁病历 2026-07-24):被 timer_expired 叫醒却交了空拍,
        # 三个唤醒条件从此全灭,全局死锁只能靠真人说话救。把唤醒券还回队列**一次**
        # 并附提醒;第二次仍空拍就依「没回应就等」的老规矩认了——这不是冷场闹钟
        # (无任何隐式定时),只是不许把已经响过的铃吞进空拍里。
        if not text and not calls and any(e.get("type") == "timer_expired" for e in events):
            if not self._wake_retry:
                self._wake_retry = True
                self.event_queue.append({
                    "type": "timer_expired", "requeued": True,
                    "note": "上一拍被计时器叫醒却空拍了——结算(ask 表决谁输也行)/追问/设新 timer,三选一"})
        elif text or calls:
            self._wake_retry = False
        # 束手拍检测(死锁第三变种,真机病历 2026-07-24:discard+draw 抽了新原子,
        # 嘴上只说「局长换一张!」——原子攥在手里没开出来,又没问没计时,三个唤醒
        # 条件全灭)。**故意收窄**到引擎真能看见的束手:抽了原子却没挂任何钩子。
        # 纯口头挑战后的静默等是「等待权」钦定行为(玩家会来 done/say),不许误伤——
        # 这正是当年冷场闹钟被否的原因。不是闹钟:无定时,当拍立即检查,单发不循环。
        engaged = (self.state.open_ask is not None or bool(self.state.timers)
                   or self.state.duel is not None
                   or self.state.pending_photo is not None
                   or self.state.pending_audio is not None
                   or any(pr.get("kind") == "骰盅" and pr.get("rolled") is None
                          for pr in self.state.props.values()))
        drew = any(c.get("name") == "draw_atom" and r.get("ok")
                   for c, r in zip(calls, results))
        # 弃了牌却没抽新的也算束手(真机病历:播报「撕掉换一张」然后干等)
        binned = any(c.get("name") == "state.discard" and r.get("ok")
                     for c, r in zip(calls, results))
        if any(e.get("type") != "no_closer" for e in events):
            self._closer_retry = False  # 有真实事件进来=局面是新的,提醒额度恢复
        if (not self.state.finished and (drew or binned) and not engaged
                and not self.event_queue and not self._closer_retry):
            self._closer_retry = True
            self.event_queue.append({
                "type": "no_closer",
                "note": "你刚抽了原子还攥在手里没开出来,又没问询没计时——把玩法当拍开出来,"
                        "并按【收口器】挂上收口(ask/timer/道具)。"})
        return line

    # —— 跑完一局 ——
    def run(self, max_turns: int = 60) -> dict:
        while not self.state.finished and self.marks["turns"] < max_turns:
            self.turn()
        summary = {
            "episode_summary": True,
            "turns": self.marks["turns"],
            "duration_min": round((time.time() - self._t0) / 60.0, 2),
            "laugh_events": self.marks["laugh_events"],
            "skips": self.marks["skips"],
            "would_replay_yes": None,  # 局后填,不得凭回忆批量补录(试点纪律沿用)
            "final_scores": dict(self.state.scores),
            "clamps": self.tools.clamp_log,
        }
        self._ep.write(json.dumps(summary, ensure_ascii=False) + "\n")
        self._ep.close()
        return summary
