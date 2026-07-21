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


def test_server_autoloop_drives_turns_without_client(server, monkeypatch):
    """App 时代的回合发动机:autoplay 的 llm 局由服务器自驱——
    驾驶舱页面关掉、没有任何客户端调 /api/turn,局也得自己走。"""
    import time
    tr = _CountingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(server, "/api/start", {"players": ["我", "阿伟"], "bots": {"阿伟": "显眼包"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "llm", "provider": "deepseek",
                                "autoplay": True, "autoplay_interval_s": 0.05})
    deadline = time.time() + 3
    while time.time() < deadline:
        snap, _ = call(server, "/api/state")
        if snap.get("marks", {}).get("turns", 0) >= 1:
            break
        time.sleep(0.05)
    assert snap["marks"]["turns"] >= 1, "服务器自驱应在无客户端驱动下跑出首拍"
    assert tr.calls >= 1


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


def test_play_page_served_and_view_has_phone_fields(server):
    """玩家页 /play 与它依赖的视图字段:手机上要显示正在放的歌与计时状态。"""
    from urllib.parse import quote
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": ["纸杯"], "driver": "manual", "provider": "mock",
                                "playlist": ["七里香 周杰伦"]})
    call(server, "/api/turn", {"text": "开场", "tool_use": [
        {"name": "music.play", "input": {"track": "七里香"}}]})
    v, code = call(server, f"/api/view?player={quote('甲')}")
    assert code == 200
    assert v["now_playing"] and "七里香" in v["now_playing"], "手机上要看得见正在放的歌"
    assert "timer_running" in v


def test_join_base_uses_reachable_host(server):
    """入座链接必须是手机打得开的地址。默认绑 127.0.0.1 时给 localhost 并由前端
    提示要加 --lan;绑 0.0.0.0 时给局域网 IP(lan_host 已避开 TUN/VPN 网段)。"""
    from modeb.simulator import lan_host
    call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
                                "objects": ["纸杯"], "driver": "manual", "provider": "mock"})
    snap, _ = call(server, "/api/state")
    assert snap["join_base"].startswith("http://"), "驾驶舱要拿得到入座前缀"
    assert ":" in snap["join_base"].split("//", 1)[1], "入座前缀要带端口"
    ip = lan_host()
    assert not ip.startswith("127."), f"lan_host 不该返回回环地址,拿到 {ip}"


def test_public_base_url_overrides_join_base(tmp_path, monkeypatch):
    """公网部署(Caddy 反代)时,PUBLIC_BASE_URL 必须整体覆盖入座链接前缀:
    手机在 4G/别家 Wi-Fi 上打不开局域网 IP,链接必须是 https://域名。
    尾部斜杠要吞掉(前端会自行拼 /play?...),不设该变量则行为不变。"""
    from modeb.simulator import Handler
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://yappa.example.com/")
    srv = make_server(0, tmp_path)
    try:
        assert Handler.hub.join_base == "https://yappa.example.com"
    finally:
        srv.server_close()
    # 不设(或空串)= 老行为:本机地址 + 端口,局域网玩法不受影响
    monkeypatch.setenv("PUBLIC_BASE_URL", "  ")
    srv2 = make_server(0, tmp_path)
    try:
        assert Handler.hub.join_base.startswith("http://")
        assert Handler.hub.join_base.endswith(str(srv2.server_address[1]))
    finally:
        srv2.server_close()


def test_duel_result_reaches_player_view(server):
    """对决揭晓要能到手机上。duel_result 没有 player 字段,而玩家视图按
    「无 player 就跳过」过滤事件——不特判就整条被滤掉,手机上永远看不到胜负。"""
    from urllib.parse import quote
    call(server, "/api/start", {"players": ["甲", "乙", "丙"], "minutes": 30, "wildness": 6,
                                "objects": ["杯子"], "driver": "manual", "provider": "mock"})
    call(server, "/api/turn", {"text": "对决", "tool_use": [
        {"name": "duel.start", "input": {"players": ["甲", "乙"]}}]})
    v, _ = call(server, f"/api/view?player={quote('甲')}")
    assert v["duel"] == {"vs": ["甲", "乙"], "drawn": False}, "对决方要看得见对峙状态"
    assert "draw_at" not in json.dumps(v, ensure_ascii=False), "拔枪时点不许出现在玩家视图"

    call(server, "/api/event", {"type": "tap", "player": "甲"})   # 立刻拍=抢跑
    call(server, "/api/turn", {"text": "", "tool_use": []})
    v3, _ = call(server, f"/api/view?player={quote('丙')}")       # 观战者也该看见
    de = [e for t in v3["recent"] for e in t["table"] if e.get("type") == "duel_result"]
    assert de and de[-1]["winner"] == "乙" and "抢跑" in de[-1]["reason"]


# —— 多局并发 + 房间码(上线前:一台服务器同时跑多桌) ——

def test_two_rooms_isolated(server):
    """两房间各自 state/inbox 隔离,互不串台:私发只落本房,座位名不跨房。"""
    a, ca = call(server, "/api/start", {"players": ["甲", "乙", "丙"], "minutes": 30,
        "wildness": 6, "objects": ["瓶子"], "driver": "manual", "provider": "mock"})
    b, cb = call(server, "/api/start", {"players": ["A", "B", "C"], "minutes": 30,
        "wildness": 6, "objects": ["杯子"], "driver": "manual", "provider": "mock"})
    ra, rb = a["room_code"], b["room_code"]
    assert ca == 200 and cb == 200
    assert ra and rb and ra != rb and len(ra) == 4
    assert not (set(ra) & set("0O1I")), "房间码须避开易混字符 0O1I"

    # 房间A 私发给乙;房间B 不该受影响
    _, code = call(server, "/api/turn", {"room": ra, "text": "发牌", "tool_use": [
        {"name": "show", "input": {"content": "暗号:芒果", "visibility": "自己看", "player": "乙"}}]})
    assert code == 200

    # 多房间不带 room → 409 要求指定
    _, code = call(server, "/api/state")
    assert code == 409, "多房间下不带 room 应 409"

    sa, _ = call(server, "/api/state?room=" + ra)
    sb, _ = call(server, "/api/state?room=" + rb)
    assert sa["players"] == ["甲", "乙", "丙"] and sb["players"] == ["A", "B", "C"]
    assert sa["inbox_counts"] == {"乙": 1}, "私发只落房间A"
    assert sb["inbox_counts"] == {}, "房间B 不该有任何私件"

    # 座位隔离:乙在房间B 是未知座位;在房间A 能取到原文
    _, code = call(server, "/api/inbox?room=" + rb + "&player=" + urllib.parse.quote("乙"))
    assert code == 400, "跨房查座位应 400 未知座位"
    box, code = call(server, "/api/inbox?room=" + ra + "&player=" + urllib.parse.quote("乙"))
    assert code == 200 and any("芒果" in x for x in box["inbox"])


def test_room_code_seating_and_default_backcompat(server):
    """房间码入座 + 默认唯一房间向后兼容;不存在的房间码 404。"""
    a, _ = call(server, "/api/start", {"players": ["甲", "乙"], "minutes": 30, "wildness": 6,
        "objects": ["瓶子"], "driver": "manual", "provider": "mock"})
    ra = a["room_code"]
    # 单房:不带 room 默认命中(现有测试少改的向后兼容)
    s, code = call(server, "/api/state")
    assert code == 200 and s["room_code"] == ra
    # 带正确房间码命中
    s2, code = call(server, "/api/state?room=" + ra)
    assert code == 200 and s2["players"] == ["甲", "乙"]
    # 入座校验也认房间码(GET /api/view 带 room)
    v, code = call(server, "/api/view?room=" + ra + "&player=" + urllib.parse.quote("甲"))
    assert code == 200 and v["you"] == "甲"
    # 不存在的房间码 404
    _, code = call(server, "/api/state?room=ZZZZ")
    assert code == 404


def test_budget_gate_silences_host_and_surfaces_on_table(server, monkeypatch):
    """计费闸到限:主持走静默拍(host_error 带「预算闸」),桌友跳过,台面 budget 可见,
    /api/view 不暴露预算。"""
    from urllib.parse import quote
    tr = _CountingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(server, "/api/start", {"players": ["我", "阿伟"], "bots": {"阿伟": "显眼包"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "llm", "provider": "deepseek",
                                "max_llm_calls": 2})
    # 首拍:主持1 + 桌友1 = 2 次,刚好到限
    call(server, "/api/turn", {})
    snap, _ = call(server, "/api/state")
    assert snap["budget"]["limit"] == 2 and snap["budget"]["used"] == 2

    used_before = tr.calls
    body, status = call(server, "/api/turn", {})
    assert status == 200 and body["host_silent"] is True
    assert "预算闸" in body["host_error"], f"静默拍原因须点明预算闸,实际 {body}"
    assert tr.calls == used_before, "到限后主持+桌友都不该再烧任何调用"

    snap2, _ = call(server, "/api/state")
    assert snap2["budget"]["gated"] is True

    v, _ = call(server, f"/api/view?player={quote('我')}")
    assert "budget" not in json.dumps(v, ensure_ascii=False), "玩家视图不得暴露预算"


def test_budget_zero_means_unlimited(server, monkeypatch):
    """max_llm_calls=0 = 不限:多跑几拍都不合闸。"""
    tr = _CountingTransport()
    monkeypatch.setattr("modeb.simulator.make_transport", lambda *a, **k: tr)
    call(server, "/api/start", {"players": ["我", "阿伟"], "bots": {"阿伟": "显眼包"},
                                "minutes": 30, "wildness": 6, "objects": ["瓶子"],
                                "driver": "llm", "provider": "deepseek",
                                "max_llm_calls": 0})
    for _ in range(3):
        body, _ = call(server, "/api/turn", {})
        assert not body.get("host_silent"), "不限档不该出现预算静默拍"
    snap, _ = call(server, "/api/state")
    assert snap["budget"]["limit"] == 0 and snap["budget"]["gated"] is False
    assert snap["budget"]["used"] >= 6
