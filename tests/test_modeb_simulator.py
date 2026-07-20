"""M2 模拟台验收:任意机位(2–10)、事件注入、manual 回合、episode 落盘。零浏览器纯 HTTP。"""
import json
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.simulator import MAX_PLAYERS, make_server  # noqa: E402


@pytest.fixture()
def server(tmp_path):
    srv = make_server(0, tmp_path)  # 端口 0 随机可用端口
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base, path, body=None):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def _start(base, n_players, driver="manual"):
    players = [f"玩家{i}" for i in range(1, n_players + 1)]
    return call(base, "/api/start", {"players": players, "minutes": 30, "wildness": 6,
                                     "objects": ["瓶子", "冰块", "手机"], "driver": driver})


def test_arbitrary_player_count(server):
    for n in (2, 5, 10):
        snap, code = _start(server, n)
        assert code == 200 and len(snap["players"]) == n
    bad, code = _start(server, 11)
    assert code == 400 and str(MAX_PLAYERS) in bad["error"]


def test_manual_turn_and_events(server):
    _start(server, 5)
    call(server, "/api/event", {"type": "laugh", "player": "玩家3"})
    call(server, "/api/event", {"type": "vote", "player": "玩家2", "value": "赞成"})
    line, code = call(server, "/api/turn", {
        "text": "开局!", "tool_use": [{"name": "state.next_round", "input": {}}]})
    assert code == 200 and line["events_in"][0]["type"] == "laugh"
    snap, _ = call(server, "/api/state")
    assert snap["digest"]["round"] == 1 and snap["marks"]["laugh_events"] == 1


def test_clamp_surface_in_state(server):
    _start(server, 4)
    call(server, "/api/turn", {"text": "", "tool_use": [
        {"name": "state.add_score", "input": {"player": "玩家1", "delta": 9}}]})
    snap, _ = call(server, "/api/state")
    assert snap["clamps"] and "越界" in snap["clamps"][-1]["clamped"]
    assert snap["digest"]["scores"]["玩家1"] == 0


def test_finish_writes_summary(server, tmp_path):
    _start(server, 3)
    call(server, "/api/turn", {"text": "收!", "tool_use": [{"name": "state.finish", "input": {}}]})
    snap, _ = call(server, "/api/state")
    assert snap["finished"] and snap["recent_turns"][-1]["episode_summary"]
    lines = Path(snap["episode_path"]).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["episode_summary"] is True
    _, code = call(server, "/api/turn", {"text": "再来?", "tool_use": []})
    assert code == 409, "局已收须拒绝再执行回合"


def test_scripted_driver_full_game_via_http(server):
    _start(server, 3, driver="scripted")
    for _ in range(12):
        _, code = call(server, "/api/turn", {})
        if code == 409:
            break
    snap, _ = call(server, "/api/state")
    assert snap["finished"], "scripted 驱动应在模拟台上跑完整局"


class _CountingTransport:
    """记账用假传输:每次模型调用 +1,返回一个合法的空决策。

    用来证明某条路径「不烧 API」——断言必须配对照组,
    否则一个从不调用任何东西的测试也能通过,等于没测。
    """

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()  # 桌友并行反应后,计数必须防竞态

    def complete(self, system, messages):
        with self._lock:
            self.calls += 1
        return json.dumps({"text": "继续。", "tool_use": []}, ensure_ascii=False)


def _start_llm_table(base, monkeypatch):
    """llm 主持 + 两个 bot 座位,全部挂同一个记账传输。"""
    tr = _CountingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(base, "/api/start", {"players": ["我", "阿伟", "琳琳"],
                              "bots": {"阿伟": "显眼包", "琳琳": "气氛组组长"},
                              "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                              "driver": "llm", "provider": "deepseek"})
    return tr


def test_finish_costs_no_api_call(server, monkeypatch):
    """收局必须是零模型调用:llm 驱动下 UI 的 tool_use 会被丢弃,
    走 /api/turn 收局既收不掉、还白烧一次钱。"""
    tr = _start_llm_table(server, monkeypatch)
    assert tr.calls == 0, "开局本身不该调用模型"

    body, status = call(server, "/api/finish", {})
    assert status == 200 and body["finished"] is True
    assert tr.calls == 0, f"收局烧了 {tr.calls} 次模型调用,应为 0"

    snap, _ = call(server, "/api/state")
    assert snap["finished"] is True, "收局后 finished 必须为真"

    call(server, "/api/finish", {})          # 重复收局
    assert tr.calls == 0, "重复收局仍不该调用模型"


def test_turn_does_cost_api_calls(server, monkeypatch):
    """对照组:证明上面那个计数器真的会动——否则零调用的断言毫无意义。"""
    tr = _start_llm_table(server, monkeypatch)
    call(server, "/api/turn", {})
    assert tr.calls >= 3, f"一回合应有 1 次主持 + 2 次座位调用,实际 {tr.calls}"


# —— 私发防偷看(可见性引擎 §1.3 落地):遮蔽必须在服务端成立 ——

def _inbox(base, player):
    return call(base, "/api/inbox?player=" + urllib.parse.quote(player))


def _show(base, content, visibility, player=None):
    inp = {"content": content, "visibility": visibility}
    if player is not None:
        inp["player"] = player
    return call(base, "/api/turn", {"text": "", "tool_use": [{"name": "show", "input": inp}]})


def test_private_show_routes_only_to_target(server):
    """自己看:内容只进目标收件箱;回合响应/轮询面一律遮蔽;episode 留全文审计。"""
    _start(server, 4)
    line, code = _show(server, "秘密任务:学猫叫三声", "自己看", "玩家2")
    assert code == 200
    disp = line["results"][0]["result"]["display"]
    assert "学猫叫" not in disp and "私发" in disp, f"回合响应必须遮蔽私发内容,实际: {disp}"

    box, _ = _inbox(server, "玩家2")
    assert any("学猫叫" in x for x in box["inbox"]), "目标座位收件箱应有原文"
    for other in ("玩家1", "玩家3", "玩家4"):
        box, _ = _inbox(server, other)
        assert not box["inbox"], f"{other} 不是目标,收件箱应为空"

    snap, _ = call(server, "/api/state")
    assert "学猫叫" not in json.dumps(snap["recent_turns"], ensure_ascii=False), \
        "轮询面(recent_turns)不得出现私发原文"
    assert snap["inbox_counts"] == {"玩家2": 1}
    assert "学猫叫" in Path(snap["episode_path"]).read_text(encoding="utf-8"), \
        "episode 文件须保留全文(审计线)"


def test_batch_private_show_routes_to_each_target(server):
    """批量私发(发牌):同一内容一次投 N 人,各自入箱;轮询面遮内容不遮收件人。"""
    _start(server, 4)
    line, code = call(server, "/api/turn", {"text": "发平民词", "tool_use": [
        {"name": "show", "input": {"content": "词:西瓜", "visibility": "自己看",
                                   "players": ["玩家2", "玩家3", "玩家4"]}}]})
    assert code == 200
    disp = line["results"][0]["result"]["display"]
    assert "西瓜" not in disp and "3 人" in disp
    for p in ("玩家2", "玩家3", "玩家4"):
        box, _ = _inbox(server, p)
        assert any("西瓜" in x for x in box["inbox"]), f"{p} 应收到批量私发"
    box, _ = _inbox(server, "玩家1")
    assert not box["inbox"], "不在名单里的人不得收到"


def test_forehead_show_routes_to_everyone_but_target(server):
    """额头牌:目标本人看不见,其余所有人收件箱都有。"""
    _start(server, 4)
    _show(server, "词:美人鱼", "额头", "玩家1")
    box, _ = _inbox(server, "玩家1")
    assert not box["inbox"], "额头牌目标本人不得收到内容"
    for other in ("玩家2", "玩家3", "玩家4"):
        box, _ = _inbox(server, other)
        assert any("美人鱼" in x and "玩家1" in x for x in box["inbox"]), \
            f"{other} 应收到玩家1的额头牌内容"
    snap, _ = call(server, "/api/state")
    assert "美人鱼" not in json.dumps(snap["recent_turns"], ensure_ascii=False)


def test_private_show_without_target_is_clamped(server):
    """自己看/额头缺目标座位 = 钳制:局照转,证据入 clamp_log,不投递任何收件箱。"""
    _start(server, 3)
    line, code = _show(server, "无主的秘密", "自己看")
    assert code == 200 and line["results"][0]["ok"] is False
    snap, _ = call(server, "/api/state")
    assert any("在座玩家" in c["clamped"] for c in snap["clamps"])
    assert snap["inbox_counts"] == {}, "钳制掉的私发不得投递"
    box, code = call(server, "/api/inbox?player=%E9%99%8C%E7%94%9F%E4%BA%BA")
    assert code == 400, "未知座位查收件箱应 400"


class _RecordingTransport:
    """记录每次调用收到的用户消息,回一个合法空事件——用来验证桌友到底看见了什么。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def complete(self, system, messages):
        self.messages.append(messages[-1]["content"])
        return '{"events": []}'


def test_bots_see_redacted_line_but_own_inbox(server, monkeypatch):
    """桌友座位拿到的回合行必须是遮蔽版;私发原文只经由目标本人的收件箱进入其上下文。
    否则 bot 座位(以及未来产品端玩家面)一轮就把底牌看光。"""
    tr = _RecordingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(server, "/api/start", {"players": ["我", "阿伟", "琳琳"],
                                "bots": {"阿伟": "显眼包", "琳琳": "气氛组组长"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "manual", "provider": "deepseek"})
    _show(server, "秘密暗号:芒果", "自己看", "阿伟")
    by_seat = {json.loads(m)["you"]: m for m in tr.messages}
    assert "芒果" in by_seat["阿伟"], "目标座位应通过私密收件拿到原文"
    assert "芒果" not in by_seat["琳琳"], "非目标座位不得见到私发原文"


def test_host_failure_becomes_silent_beat_not_500(server, monkeypatch):
    """主持调用挂了:错误不进游戏,只进台面。/api/turn 回 200 的沉默拍,
    真相在 snapshot 的 host_errors 仪表里;服务活着,局没死,事件不丢。"""
    class _Exploding:
        def complete(self, system, messages):
            raise RuntimeError("模拟 429 限流")

    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: _Exploding())
    call(server, "/api/start", {"players": ["我", "阿伟"], "bots": {"阿伟": "显眼包"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "llm", "provider": "deepseek"})
    call(server, "/api/event", {"type": "done", "player": "我"})

    body, status = call(server, "/api/turn", {})
    assert status == 200 and body["host_silent"] is True
    assert "429" in body["host_error"], f"错误原因要留痕,实际 {body}"

    snap, code = call(server, "/api/state")     # 服务必须还活着
    assert code == 200 and snap["finished"] is False
    assert snap["host_errors"]["count"] == 1 and "429" in snap["host_errors"]["last"]
    assert snap["pending_events"], "沉默拍不得吞事件——玩家按的「完成」还在队列里"
    assert snap["turn_ready"] is False, "冷却期内自动循环不该再撞"


def _view(base, player):
    from urllib.parse import quote
    return call(base, f"/api/view?player={quote(player)}")


def test_player_view_hides_everything_not_on_your_phone(server):
    """玩家视图 = 一台手机该看见的东西。房主裁定:模拟台只有驾驶舱一个口时,
    玩家为了看「到我了吗」被迫读它,顺带撞见私件元数据——泄漏面是测试工具
    带进去的。三条注记:带进度、别人的 to=局长 不给内容、不含调试字段。"""
    call(server, "/api/start", {"players": ["甲", "乙", "丙"], "minutes": 30, "wildness": 6,
                                "objects": ["扑克牌"], "driver": "manual", "provider": "mock"})
    call(server, "/api/turn", {"text": "发牌", "tool_use": [
        {"name": "show", "input": {"content": "你是卧底", "visibility": "自己看", "player": "乙"}},
        {"name": "random.int", "input": {"min": 1, "max": 6, "visibility": "自己看", "player": "乙"}}]})
    call(server, "/api/event", {"type": "say", "player": "丙", "text": "局长这不公平", "to": "局长"})
    call(server, "/api/event", {"type": "say", "player": "甲", "text": "丙又开始了", "to": "桌上"})
    call(server, "/api/turn", {"text": "稍等", "tool_use": []})

    v_jia, code = _view(server, "甲")
    assert code == 200
    # 进度必须给,否则玩家还得回驾驶舱看 /api/state,泄漏面等于没堵
    for k in ("turn", "turn_ready", "focus", "round", "scores", "scene_objects"):
        assert k in v_jia, f"玩家视图缺进度字段 {k}"
    # 调试字段一个都不许有
    for k in ("tool_use", "results", "inbox_counts", "clamps", "pending_events"):
        assert k not in v_jia, f"玩家视图不该含调试字段 {k}"
    # 别人的私件内容与收件人都不出现
    blob = json.dumps(v_jia, ensure_ascii=False)
    assert "你是卧底" not in blob, "别人的私件内容泄漏进了玩家视图"
    assert v_jia["inbox"] == [], "甲没收到过私件,收件箱该是空的"
    # 别人的定向发言只留痕不留字
    tbl = [e for t in v_jia["recent"] for e in t["table"] if e["player"] == "丙"]
    assert tbl and "text" not in tbl[0] and tbl[0].get("note"), "别人的 to=局长 不该给内容"
    # 桌上互说照常听得见
    assert any(e.get("text") == "丙又开始了" for t in v_jia["recent"] for e in t["table"])

    # 本人视角:自己的私件全文可见,自己说的话也看得见
    v_yi, _ = _view(server, "乙")
    assert any("你是卧底" in s for s in v_yi["inbox"])
    v_bing, _ = _view(server, "丙")
    assert any(e.get("text") == "局长这不公平"
               for t in v_bing["recent"] for e in t["table"] if e["player"] == "丙")


def test_player_view_rejects_unknown_seat(server):
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": ["扑克牌"], "driver": "manual", "provider": "mock"})
    _, code = _view(server, "路人")
    assert code == 400
