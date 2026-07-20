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
    assert captured["payload"]["system"] == [
        {"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]  # 缓存块:每拍省一遍全量预填
    assert captured["payload"]["model"].startswith("claude-haiku")


def test_openai_compat_payload_shape(monkeypatch):
    """国产口走流式(深度思考模型开思考=强制流式);断言请求形状 + SSE 聚合。"""
    from modeb import transports
    import io
    captured = {}

    class _FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=90):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["payload"] = json.loads(req.data.decode())
        # 两块 SSE:思考痕迹(reasoning_content,应丢)+ 正式答案(content,应收)
        sse = (b'data: {"choices":[{"delta":{"reasoning_content":"\xe5\x97\xaf"}}]}\n'
               b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'
               b'data: [DONE]\n')
        return _FakeResp(sse)

    monkeypatch.setattr(transports.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dk-test")
    t = transports.make_transport("deepseek")
    assert t.complete("SYS", [{"role": "user", "content": "hi"}]) == "ok", "只收 content,丢 reasoning"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["stream"] is True, "国产口必须流式,否则思考模型报 only-stream"
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
    n_skill_lib = sum(1 for _ in open("inputs/skills/skills-v1.jsonl", encoding="utf-8"))
    assert len(pool) == len(SEED_ATOMS) + 1 + n_skill_lib  # 种子 + 抽取件 + 技能单独库


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
    # 分档解耦后:开局款通用局(opener)= 铺垫拍;旧规则把完整玩法整类判成主打,
    # 导致「通用局+铺垫」组合必空返(三桌 2331 条只抽出 2 条的根因),不许回退。
    assert next(a for a in pool if a["name"] == "快枪手对决")["tier"] == "铺垫"
    assert next(a for a in pool if a["name"] == "三刀流")["tier"] == "主打", \
        "长流程完整玩法仍是主打——解耦不等于全变铺垫"


def test_draw_atom_tier_filter(tmp_path):
    from modeb.engine import Engine
    from modeb.driver_scripted import ScriptedDriver
    from modeb.state import GameState
    state = GameState(players=["甲", "乙", "丙"], wildness_cap=6, time_budget_min=30,
                      scene_objects=["瓶子", "手机"])
    eng = Engine(state, ScriptedDriver(), tmp_path / "ep.jsonl", rng_seed=3)
    r = eng.tools.execute({"name": "draw_atom", "input": {"tier": "主打", "野度": 6}})
    assert r["ok"] and r["result"]["atom"]["tier"] == "主打"

class _BrokenTransport:
    """永远 401 的传输——模拟 key 错/模型串过期。"""

    def complete(self, system, messages):
        raise RuntimeError("HTTP 401:{\"error\":{\"message\":\"Authentication Fails\"}}")


def test_seat_failure_is_loud_but_not_fatal(capsys):
    """桌友掉线不许卡局,但必须留下痕迹——静默降级会把查错方向带偏。"""
    from modeb.player_agent import LLMPlayerAgent

    bot = LLMPlayerAgent("阿伟", "显眼包", _BrokenTransport())
    line, digest = {"text": "来吧", "results": []}, {"round": 1, "scores": {}}

    assert bot.react(line, digest) == [], "失败时应返回空事件,不抛给上层"
    assert bot.errors == 1
    assert "401" in bot.last_error

    err = capsys.readouterr().err
    assert "阿伟" in err and "401" in err, f"首次失败必须吼到 stderr,实际:{err!r}"

    for _ in range(5):                       # 后续失败静默累计,不刷屏
        bot.react(line, digest)
    assert bot.errors == 6
    assert capsys.readouterr().err == "", "重复同类错误不该继续播报"
