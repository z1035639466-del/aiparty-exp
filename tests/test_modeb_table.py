"""M2 三/四单验收:传输层 payload 形状、桌友 agent 解析纪律、整桌编排、模拟台 bot 座位。"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from modeb.driver_llm import MockTransport  # noqa: E402
from modeb.driver_scripted import ScriptedDriver  # noqa: E402
from modeb.engine import Engine  # noqa: E402
from modeb.player_agent import LLMPlayerAgent, ScriptedPlayerAgent, parse_player_events  # noqa: E402
from modeb.state import GameState  # noqa: E402
from modeb.table import TableRunner  # noqa: E402
from modeb.simulator import make_server  # noqa: E402


# —— 传输层:不碰网络,截获 payload 验形状 ——
def test_anthropic_payload_shape(monkeypatch):
    from modeb import transports
    captured = {}

    def fake_post(url, headers, payload, timeout=60):
        captured.update(url=url, headers=headers, payload=payload)
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(transports, "_post_json", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    t = transports.AnthropicTransport("haiku")
    out = t.complete("SYS", [{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "k-test"
    assert captured["payload"]["system"] == "SYS"
    assert captured["payload"]["model"].startswith("claude-haiku")


def test_openai_compat_payload_shape(monkeypatch):
    from modeb import transports
    captured = {}

    def fake_post(url, headers, payload, timeout=60):
        captured.update(url=url, headers=headers, payload=payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(transports, "_post_json", fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk-test")
    t = transports.make_transport("deepseek")
    assert t.complete("SYS", [{"role": "user", "content": "hi"}]) == "ok"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["payload"]["model"] == "deepseek-v4-pro"


def test_cn_provider_registry_matches_bidding_config():
    """五家注册表与 run_cn_bidding.py(aiparty-cn-bidding-rerun-20260715)同源核对。"""
    from modeb.transports import CN_PROVIDERS, make_transport
    import run_cn_bidding as bidding
    by_base = {p.base_url: p.requested_model for p in bidding.PROVIDERS} \
        if hasattr(bidding, "PROVIDERS") else None
    expect = {
        "minimax": ("https://api.minimaxi.com/v1", "MiniMax-M2.7"),
        "kimi": ("https://api.moonshot.cn/v1", "kimi-k2.6"),
        "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.1"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.7-plus"),
        "deepseek": ("https://api.deepseek.com", "deepseek-v4-pro"),
    }
    for name, (base, model) in expect.items():
        assert CN_PROVIDERS[name]["base"] == base
        assert CN_PROVIDERS[name]["model"] == model
        t = make_transport(name)
        assert t.base == base and t.model == model
        if by_base is not None:
            assert by_base.get(base) == model, f"{name} 与竞标脚本配置漂移"


def test_transport_requires_key(monkeypatch):
    from modeb.transports import AnthropicTransport
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport().complete("s", [])


# —— 桌友 agent 纪律 ——
def test_player_events_identity_pinned():
    raw = json.dumps({"events": [
        {"type": "vote", "value": "赞成", "player": "假冒者"},
        {"type": "laugh"}, {"type": "tap"}]}, ensure_ascii=False)
    evs = parse_player_events(raw, "小静")
    assert len(evs) == 2, "事件截断 ≤2"
    assert all(e["player"] == "小静" for e in evs), "座位身份系统钉死,不得冒名"


def test_player_agent_bad_output_is_silence():
    agent = LLMPlayerAgent("大鹏", "吐槽王", MockTransport(["不是JSON的废话"]))
    assert agent.react({"text": "x", "results": []}, {"round": 1, "scores": {}}) == []


def test_player_agent_transport_error_no_crash():
    class Boom:
        def complete(self, *a):
            raise RuntimeError("网络炸了")
    agent = LLMPlayerAgent("阿伟", "显眼包", Boom())
    assert agent.react({"text": "x", "results": []}, {"round": 1, "scores": {}}) == []


def test_disallowed_event_type_filtered():
    raw = json.dumps({"events": [{"type": "state.add_score", "delta": 3}]})
    assert parse_player_events(raw, "老宋") == [], "桌友只许出玩家事件,不许摸工具"


# —— 整桌编排:主持脚本 + 桌友脚本,事件在下一回合聚合 ——
def test_table_runner_bot_events_next_turn(tmp_path):
    state = GameState(players=["疯子明", "小静", "大鹏"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子", "冰块", "纸巾", "手机", "杯子", "打火机"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=7)
    bots = [ScriptedPlayerAgent("小静", [[{"type": "laugh"}]] * 12)]
    runner = TableRunner(eng, bots)
    runner.run_turn()
    line2 = runner.run_turn()
    assert any(e.get("type") == "laugh" and e.get("player") == "小静"
               for e in line2["events_in"]), "bot 事件须在下一回合聚合"
    summary = runner.run_to_finish()
    assert state.finished and summary["laugh_events"] >= 9


# —— 模拟台 bot 座位(provider=mock 确定性假人) ——
@pytest.fixture()
def server(tmp_path):
    srv = make_server(0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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


def test_simulator_bot_seats(server):
    snap, code = call(server, "/api/start", {
        "players": ["真人", "波特", "琳琳"], "minutes": 30, "wildness": 6,
        "objects": ["瓶子"], "driver": "manual", "provider": "mock",
        "bots": {"波特": "显眼包", "琳琳": "气氛组"}})
    assert code == 200 and snap["bots"] == ["波特", "琳琳"]
    call(server, "/api/turn", {"text": "开场", "tool_use": [{"name": "state.next_round", "input": {}}]})
    snap, _ = call(server, "/api/state")
    pend = [(e.get("type"), e.get("player")) for e in snap["pending_events"]]
    assert ("laugh", "波特") in pend and ("laugh", "琳琳") in pend


def test_simulator_rejects_unknown_bot_seat(server):
    bad, code = call(server, "/api/start", {
        "players": ["甲", "乙"], "minutes": 30, "wildness": 6, "objects": [],
        "driver": "manual", "provider": "mock", "bots": {"丙": ""}})
    assert code == 400 and "丙" in bad["error"]


def test_atom_pool_merges_extracted_file(tmp_path):
    """M-int-1 产物落盘即被引擎并入弹药库——小红书采集真正用上的通道。"""
    import json
    from modeb.tools import load_atom_pool
    from modeb.atoms_seed import SEED_ATOMS
    f = tmp_path / "atoms-v1.jsonl"
    f.write_text("\n".join([
        json.dumps({"atom_id": "xhs-00001", "name": "门口迎宾", "atom_type": "任务内容",
                    "text_raw": "去厕所门口说欢迎光临", "wildness": 2, "props_explicit": [],
                    "safety_flags": [], "currency": "表演", "confidence": "high",
                    "source_ref": {}}, ensure_ascii=False),
        json.dumps({"atom_id": "xhs-00002", "name": "低置信件", "atom_type": "任务内容",
                    "text_raw": "?", "wildness": 3, "props_explicit": [], "safety_flags": [],
                    "currency": "表演", "confidence": "low", "source_ref": {}}, ensure_ascii=False),
    ]), encoding="utf-8")
    pool = load_atom_pool(str(f))
    ids = {a["id"] for a in pool}
    assert "xhs-00001" in ids, "high 置信抽取件须入池"
    assert "xhs-00002" not in ids, "low 置信不入主池"
    assert len(pool) == len(SEED_ATOMS) + 1


def test_score_style_switches_prompt():
    from modeb.driver_llm import build_system_prompt
    qing = build_system_prompt(["甲", "乙"], 6, 30, "清账")
    zong = build_system_prompt(["甲", "乙"], 6, 30, "综艺")
    jing = build_system_prompt(["甲", "乙"], 6, 30, "竞技")
    auto = build_system_prompt(["甲", "乙"], 6, 30, "自动")
    assert "零设置原则" in auto and "房主一句话随时改" in auto
    assert "当场清账" in qing and "不要汇总排名" in qing
    assert "可以攒分" in zong and "综艺也不围着总分第一转" in zong
    assert "真正的冠军" in jing and "对局不对人" in jing
    for sp in (qing, zong, jing):
        assert "负向人身标签" in sp, "底线不随风格变"
        assert "老梗" in sp, "时效条不随风格变"


def test_ocr_space_repair_and_tier(tmp_path):
    """①中文断行空格修复优于隔离(text_raw铁则不动,text_clean供池用);②价值分档过滤。"""
    import json
    from modeb.tools import load_atom_pool
    f = tmp_path / "atoms.jsonl"
    f.write_text(json.dumps({
        "atom_id": "xhs-t1", "name": "头发对灯", "atom_type": "任务内容",
        "text_raw": "捏住一根头发,对着灯光看,不 敢的唱。",
        "text_clean": "捏住一根头发,对着灯光看,不敢的唱。",
        "wildness": 2, "props_explicit": [], "safety_flags": [], "currency": "表演",
        "confidence": "high", "source_ref": {}}, ensure_ascii=False), encoding="utf-8")
    pool = load_atom_pool(str(f))
    atom = next(a for a in pool if a["id"] == "xhs-t1")
    assert " " not in atom["text"], "池内文本须用修复版,主持照读不再断字"
    assert atom["tier"] == "铺垫", "敢不敢型微挑战自动归铺垫档"
    assert next(a for a in pool if a["name"] == "快枪手对决")["tier"] == "主打"


def test_draw_atom_tier_filter(tmp_path):
    from modeb.engine import Engine
    from modeb.driver_scripted import ScriptedDriver
    from modeb.state import GameState
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子", "手机"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=3)
    r = eng.tools.execute({"name": "draw_atom", "input": {"tier": "主打", "野度": 6}})
    assert r["ok"] and r["result"]["atom"]["tier"] == "主打"
