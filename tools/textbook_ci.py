"""教材 CI —— 正典【params 全示例】逐块过闸门（常设脚本，每次正典修订复用）。

做四件事：
  1. 从正典 md 的【params 全示例】抽出全部编号示例 JSON（惩罚三档展开，共 15 个
     mechanic 的主示例 + 限时扣分完整变体 + 判定三源 + 声明质疑主例）——本脚本内每个
     BLOCK 直书示例原文 raw，并对正典 md 做**抽取保真核对**（raw 必是 md 子串，防漂移）。
  2. 以 tests/test_check.py 的合法基线件 valid_doc() 为壳，逐块替换 rules[0] 的
     mechanic+params；为块内每个 scoring_ref 注入同名 scoring event（effect 与档位匹配）；
     on_timeout=goto 变体为其注入同名 flow 阶段；顶层字段保持基线件齐全形态。
  3. 每块出的最小件喂 check.py v2.1（check.check_document），逐块判 过/拒。
  4. 输出逐块过/拒表 + 15 机制覆盖汇总。

验收 = 15/15 全绿（15 机制无一挂）。任一红：脚本贴 check 报错原文 + 对应示例块原文，
退出码非零。**停工勿自修——教材修改权在 Fable。** 抽取保真核对失败属本脚本抽取缺陷
（非教材缺陷），另行标记，可自修 raw 使之与正典一致。

用法：
  python3 tools/textbook_ci.py [正典md路径]
默认正典路径 docs/specs/DM-skill-v2.1.1.md（可传参覆盖，供入库前对暂存件跑绿）。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import check  # noqa: E402
from tests.test_check import WHITELIST, valid_doc  # noqa: E402

DEFAULT_SPEC = ROOT / "docs" / "specs" / "DM-skill-v2.1.1.md"

PENALTY_MECHANICS = {"惩罚(轻)", "惩罚(中)", "惩罚(重)"}
# 事件名含损失语义 → 注入 -N，否则 +N（仅为注入 event 的可读性；不影响硬闸判定）。
LOSS_KEYWORDS = ("扣", "罚", "输", "爆", "冤", "失败", "漏", "错误", "清零")

# 15 机制全集（惩罚三档各计一），用于覆盖汇总核账。
FIFTEEN_MECHANICS = {
    "点名目标", "转移", "加减分", "惩罚(轻)", "惩罚(中)", "惩罚(重)", "揭示",
    "限时", "回合推进", "声明质疑", "同时提交", "续押喊停", "受限沟通", "判定", "传递链",
}

# —— 编号示例块（raw 逐字取自正典【params 全示例】；惩罚三档展开，判定三源展开） ——
BLOCKS: list[dict] = [
    {"id": "1", "mechanic": "同时提交", "variant": "主示例",
     "raw": '{"prompt":"押谁赢？","input_kind":"options","options":"$派生:本轮对决双方","timeout_s":20,"reveal":"simultaneous","scoring_ref":["押中加分","押错扣分"]}'},
    {"id": "2", "mechanic": "限时", "variant": "主示例(goto)",
     "raw": '{"seconds":60,"visible_countdown":true,"on_timeout":{"effect":"goto","goto":"进入投票"}}'},
    {"id": "2变", "mechanic": "限时", "variant": "扣分完整变体",
     "raw": '{"seconds":45,"visible_countdown":true,"on_timeout":{"effect":"scoring","scoring_ref":["超时扣分"]}}'},
    {"id": "3共识", "mechanic": "判定", "variant": "三源·共识",
     "raw": '{"source":"consensus","question":"他这段算过关吗？","verdict_options":["过","不过"],"on":{"过":{"scoring_ref":["挑战成功"]},"不过":{"scoring_ref":["挑战失败"]}}}'},
    {"id": "3表达式", "mechanic": "判定", "variant": "三源·表达式",
     "raw": '{"source":"expr","question":"计数是否踩中 7 的倍数？","expr":"state:连击数 % 7 == 0","verdict_options":["过","不过"],"on":{"过":{"scoring_ref":["安全过拍"]},"不过":{"scoring_ref":["漏拍受罚"]}}}'},
    {"id": "3主观", "mechanic": "判定", "variant": "三源·主观",
     "raw": '{"source":"ai","question":"这句台词够不够阴阳怪气？","ai_overridable":true,"verdict_options":["过","不过"],"on":{"过":{"scoring_ref":["毒舌达标"]},"不过":{"scoring_ref":["火力不足"]}}}'},
    {"id": "4轻", "mechanic": "惩罚(轻)", "variant": "三档展开·轻",
     "raw": '{"who":"loser","pool":"$gen.penalty_内容池","scoring_ref":["闯关失败"]}'},
    {"id": "4中", "mechanic": "惩罚(中)", "variant": "三档展开·中",
     "raw": '{"who":"loser","pool":"$gen.penalty_内容池","scoring_ref":["闯关失败"]}'},
    {"id": "4重", "mechanic": "惩罚(重)", "variant": "三档展开·重",
     "raw": '{"who":"loser","pool":"$gen.penalty_内容池","scoring_ref":["闯关失败"]}'},
    {"id": "5", "mechanic": "加减分", "variant": "主示例",
     "raw": '{"who":"winner","delta":"+2","scoring_ref":["竞速夺冠"]}'},
    {"id": "6", "mechanic": "声明质疑", "variant": "主示例",
     "raw": '{"claim_prompt":"公开声称你的密语内容（允许撒谎）","challengers":"others","challenge_window_s":30,"verify_source":"prop_reveal:密语卡","verify_reveals":"prop_state","on_liar":{"scoring_ref":["撒谎受罚","揭穿加分"]},"on_false_accuse":{"scoring_ref":["冤枉受罚","被冤枉补偿"]}}'},
    {"id": "7", "mechanic": "续押喊停", "variant": "主示例",
     "raw": '{"draw_from":"$gen.事件池","continue_prompt":"继续加注还是见好就收？","bust_when":"抽中炸弹牌","cap":8,"on_cap":"force_settle","bank_on_stop":true,"scoring_ref":["落袋为安","爆掉清零"]}'},
    {"id": "8", "mechanic": "点名目标", "variant": "主示例",
     "raw": '{"selector":"vote","target_pool":"alive_others","on_named":{"scoring_ref":["放逐正确","放逐错误"],"eliminate":true}}'},
    {"id": "9", "mechanic": "转移", "variant": "主示例",
     "raw": '{"what":"score","from":"actor","to":"chosen","scoring_ref":["劫分成功"]}'},
    {"id": "10", "mechanic": "揭示", "variant": "主示例",
     "raw": '{"reveal_of":"prop:身份牌","to":"actor","once":true,"identity_resolution":"terminal"}'},
    {"id": "11", "mechanic": "回合推进", "variant": "主示例",
     "raw": '{"order":"round_robin"}'},
    {"id": "12", "mechanic": "受限沟通", "variant": "主示例",
     "raw": '{"channel":"one_word","enforce":"channel_only"}'},
    {"id": "13", "mechanic": "传递链", "variant": "主示例",
     "raw": '{"content_from":"$gen.原句池","order":"seat","each_sees":"prev_only","replay":"first_vs_last"}'},
]


def derive_events(mechanic: str, params: dict) -> list[tuple[str, str]]:
    """块内每个 scoring_ref → 同名 scoring event；惩罚块 effect 取机制档位，余取 ±N。"""
    refs = list(dict.fromkeys(check.collect_scoring_refs(params)))
    events: list[tuple[str, str]] = []
    for ref in refs:
        if mechanic in PENALTY_MECHANICS:
            effect = mechanic  # 档位一致：event.effect 必须与机制档位同档
        else:
            effect = "-2" if any(kw in ref for kw in LOSS_KEYWORDS) else "+2"
        events.append((ref, effect))
    return events


def goto_targets(params: dict) -> list[str]:
    """on_timeout=goto 变体的跳转目标（注入同名 flow 阶段）。"""
    on_timeout = params.get("on_timeout")
    if isinstance(on_timeout, dict) and on_timeout.get("effect") == "goto":
        target = on_timeout.get("goto")
        if isinstance(target, str) and target:
            return [target]
    return []


def build_doc(block: dict) -> dict:
    """以基线件为壳，逐块替换 rules[0]，注入 event / prop / flow，顶层字段保持齐全。"""
    params = json.loads(block["raw"])
    doc = copy.deepcopy(valid_doc())
    doc["rules"][0] = {
        "flavor_name": f"教材示例·{block['id']}",
        "mechanic": block["mechanic"],
        "plain_rule": "按本条 params 执行（教材示例壳，白话无数字以免抽查误报）。",
        "visibility": block.get("visibility", "全场公开"),
        "params": params,
    }
    # 注入同名 scoring event（effect 与档位匹配）
    existing = {entry["event"] for entry in doc["settlement"]["scoring"]}
    for event, effect in derive_events(block["mechanic"], params):
        if event not in existing:
            doc["settlement"]["scoring"].append({"event": event, "who": "all", "effect": effect})
            existing.add(event)
    # 块内引用的固定库道具须实发
    dealt = {prop["prop"] for prop in doc["props_dealt"]}
    for _kind, name in check.collect_prop_refs(params):
        if name not in dealt:
            doc["props_dealt"].append(
                {"prop": name, "to": "全体", "visibility": "自己看", "note": "教材示例道具"}
            )
            dealt.add(name)
    # goto 类变体注入同名 flow 阶段
    for target in goto_targets(params):
        if target not in doc["flow"]:
            doc["flow"].append(target)
    return doc


def fidelity_check(spec_text: str) -> list[str]:
    """抽取保真核对：每个 raw 必是正典 md 子串（防抽取漂移）。返回缺失块 id 列表。"""
    return [block["id"] for block in BLOCKS if block["raw"] not in spec_text]


def run() -> int:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC
    print(f"教材 CI · check 实现规范 {check.SPEC_VERSION} · 正典 {spec_path}")
    print("=" * 72)

    # —— 抽取保真核对（本脚本抽取 vs 正典 md） ——
    fidelity_missing: list[str] = []
    if spec_path.is_file():
        spec_text = spec_path.read_text(encoding="utf-8")
        fidelity_missing = fidelity_check(spec_text)
        if fidelity_missing:
            print(f"⚠ 抽取保真核对失败（本脚本抽取缺陷，非教材缺陷）: 块 {', '.join(fidelity_missing)} 的 raw 不是正典子串")
        else:
            print(f"抽取保真核对: {len(BLOCKS)}/{len(BLOCKS)} raw 全为正典 md 子串 ✓")
    else:
        print(f"⚠ 正典 md 不存在，跳过抽取保真核对: {spec_path}")
    print("-" * 72)

    # —— 逐块过闸 ——
    reds: list[tuple[dict, list[str]]] = []
    mechanics_seen: set[str] = set()
    mechanics_red: set[str] = set()
    for block in BLOCKS:
        doc = build_doc(block)
        result = check.check_document(doc, WHITELIST)
        mechanics_seen.add(block["mechanic"])
        label = f"[{block['id']:>6}] {block['mechanic']:<8} {block['variant']}"
        if result.errors:
            reds.append((block, result.errors))
            mechanics_red.add(block["mechanic"])
            print(f"拒 {label}")
        else:
            warn = f"  （软闸 {len(result.warnings)} 条，不拒件）" if result.warnings else ""
            print(f"过 {label}{warn}")
    print("-" * 72)

    # —— 15 机制覆盖汇总 ——
    missing_cover = sorted(FIFTEEN_MECHANICS - mechanics_seen)
    green_mechanics = FIFTEEN_MECHANICS - mechanics_red
    print(f"机制覆盖: {len(mechanics_seen & FIFTEEN_MECHANICS)}/15 出场"
          + (f"；未覆盖 {missing_cover}" if missing_cover else "（15 全覆盖）"))
    print(f"机制过闸: {len(green_mechanics)}/15 全绿"
          + (f"；红档 {sorted(mechanics_red)}" if mechanics_red else "（15/15 全绿）"))
    print(f"逐块统计: {len(BLOCKS) - len(reds)}/{len(BLOCKS)} 块过")

    # —— 红档贴报错原文 + 示例块原文 ——
    if reds:
        print("=" * 72)
        print("红档明细（停工勿自修，教材修改权在 Fable）：")
        for block, errors in reds:
            print(f"\n● 块 [{block['id']}] {block['mechanic']} · {block['variant']}")
            print(f"  示例块原文: {block['raw']}")
            for error in errors:
                print(f"  check 报错: {error}")

    ok = not reds and not fidelity_missing and not missing_cover \
        and len(green_mechanics) == 15
    print("=" * 72)
    print("结论: 15/15 全绿，教材 CI 通过，可入库。" if ok else "结论: 未达 15/15 全绿——不入库。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
