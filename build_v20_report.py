#!/usr/bin/env python3
"""Build the frozen v2.0 retry-batch tables from repository artifacts.

This is deliberately a reporting-only tool.  It does not mutate candidates,
reviews, the retry manifest, or usage_log.csv.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent
MAIN_MODELS = ("sonnet", "haiku", "dsT")
REVIEW_HEADER = re.compile(r"^VERDICT:\s*([^|\n]+)(.*)$", re.M)
INFLATION = re.compile(r"膨胀:\s*原\s*(\d+)\s*条?→有效\s*(\d+)")
ITEM = re.compile(r"(?ms)^\s*(\d+)\.\s*(.*?)(?=^\s*\d+\.|^总评：|\Z)")

# 正典名取自 docs/36家族机制三型归属表.md。映射依据是核心循环，
# 不是表层 flavor_name；无法稳定归类的条目明确留待人工复核。
FAMILY_RULES = (
    ("身份心理战/隐藏角色社交推理", ("隐藏身份", "阵营", "卧底", "找出.*王", "放逐")),
    ("默契问答比对", ("默契", "同时.*选择", "答案一致", "匹配")),
    ("才艺表演公投评选", ("表演", "展示", "公投", "评优")),
    ("真心话/自曝陈述", ("故事", "陈述", "自曝", "真心话")),
    ("表演猜词", ("猜词", "动作", "演绎")),
    ("背景禁语监听淘汰", ("禁词", "禁止词", "说出.*词")),
    ("隐藏信息叫价诈唬", ("叫价", "加码", "诈唬")),
    ("二十问/猜身份排除游戏", ("标签", "是/否", "排除")),
    ("点名弹劾表决惩罚", ("点名", "举报", "表决", "指认")),
    ("记忆链复述/传话失真", ("传话", "复述", "首尾", "漂流")),
    ("命运轮盘/随机选中", ("轮盘", "随机抽", "抽签")),
    ("大冒险/表演挑战", ("挑战", "任务")),
    ("知识问答竞猜擂台", ("问答", "抢答", "标准答案")),
)

BASELINE_FAMILY_MAP = {
    "自我隐藏身份猜测+挑战": "二十问/猜身份排除游戏",
    "自我隐藏身份同步猜测": "二十问/猜身份排除游戏",
    "密语诱导+指认投票": "点名弹劾表决惩罚",
    "密语诱导+质疑投票": "背景禁语监听淘汰",
    "阵营身份+放逐": "身份心理战/隐藏角色社交推理",
    "卧底放逐": "身份心理战/隐藏角色社交推理",
    "隐藏身份放逐": "身份心理战/隐藏角色社交推理",
    "密语诱导+投票计分": "点名弹劾表决惩罚",
    "禁词诱导+投票判定": "背景禁语监听淘汰",
    "叙事欺诈+真假投票": "真心话/自曝陈述",
    "黑话陷阱+同步指认": "点名弹劾表决惩罚",
    "密语带词+投票验证": "背景禁语监听淘汰",
    "抽签表演+群体判定": "才艺表演公投评选",
    "转盘挑战+身份竞猜": "命运轮盘/随机选中",
    "表演欺诈+身份竞猜": "表演猜词",
    "自我身份猜测+密令触发": "二十问/猜身份排除游戏",
    "他人身份竞猜+挑战": "二十问/猜身份排除游戏",
    "他人信息竞猜+提示奖励": "二十问/猜身份排除游戏",
    "挑战计分+自我身份猜测": "二十问/猜身份排除游戏",
}


def model_of(name: str) -> str:
    if name.startswith("ds_B_div"):
        return "ds_B_div"
    return name.split("_", 1)[0]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_checker(root: Path) -> Callable[[Path], tuple[bool, list[str]]]:
    spec = importlib.util.spec_from_file_location("frozen_check_v20", root / "check.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    whitelist = load_json(root / "whitelist.json")

    def check_path(path: Path) -> tuple[bool, list[str]]:
        try:
            doc = load_json(path)
        except (OSError, json.JSONDecodeError):
            return False, []
        result = module.check_document(doc, whitelist)
        return not result.errors, list(result.warnings)

    return check_path


def parse_review(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    match = REVIEW_HEADER.search(text)
    header = match.group(2) if match else ""
    fields = {k: v.strip() for k, v in re.findall(r"(死规则|坍缩|真空|伪猜|占优|终局|结算|落点|融合|小局身份|shape):\s*([^\s|]+)", header)}
    inf = INFLATION.search(header)
    items = {int(n): body.strip() for n, body in ITEM.findall(text)}
    return {
        "file": path.name,
        "model": model_of(path.name),
        "verdict": match.group(1).strip() if match else "缺失",
        "fields": fields,
        "inflation": {"original": int(inf.group(1)), "effective": int(inf.group(2))} if inf else None,
        "items": items,
    }


def retry_paths(root: Path, job: dict) -> list[Path]:
    source = Path(job["source"])
    stem = source.stem
    return [root / "outputs" / f"{stem}_r{n}.json" for n in range(job["start_retry"], job["start_retry"] + job["max_new_attempts"])]


def final_candidate(root: Path, job: dict, check_path) -> tuple[Path | None, str]:
    """Last valid new attempt; absent/invalid attempts remain explicitly pending."""
    candidates = retry_paths(root, job)
    existing = [p for p in candidates if p.exists()]
    valid = [p for p in existing if check_path(p)[0]]
    if valid:
        return valid[-1], "complete"
    if len(existing) < len(candidates):
        return None, "pending"
    return None, "exhausted_invalid"


def review_for(candidate: Path) -> Path:
    return candidate.with_name(candidate.name + ".review3.md")


def ratio(n: int, d: int) -> dict:
    return {"passed": n, "total": d, "rate": round(n / d, 6) if d else None}


def core_loop(doc: dict) -> str:
    rules = doc.get("rules") or []
    mechanics = [r.get("mechanic", "") for r in rules[:3] if isinstance(r, dict)]
    flow = doc.get("flow") or []
    short = "→".join(str(x).strip() for x in flow[:3])
    return (" + ".join(filter(None, mechanics)) + ("；" if mechanics and short else "") + short)[:180]


def canonical_family(text: str) -> str:
    for family, patterns in FAMILY_RULES:
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            return family
    return "边界件·待人工复核"


def baseline_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    line_re = re.compile(r"^\|?\s*(ds_B_div_\d+(?:\s*\(r\))?)\s*\|\s*(shape:[^|]+)\|\s*([^|]+)\|\s*([^|]+)\|?")
    # The historical file is a captured terminal artifact and contains literal
    # ``\n`` separators, so normalize those before parsing its table.
    content = path.read_text(encoding="utf-8-sig").replace("\\n", "\n")
    for line in content.splitlines():
        match = line_re.match(line)
        if not match:
            continue
        raw = match.group(4).strip()
        normalized = raw.replace("＋", "+")
        rows.append({"id": match.group(1), "shape": match.group(2).replace("shape:", "").strip(), "loop": match.group(3).strip(), "raw_family": raw, "canonical_family": BASELINE_FAMILY_MAP.get(normalized, canonical_family(match.group(3) + raw))})
    return rows


def canonical_catalog(path: Path) -> list[str]:
    """Read the authoritative 36-family names (used as an output audit)."""
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            names.append(match.group(1).replace("**", "").strip())
    return names


def usage_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def build_data(root: Path, check_path=None) -> dict:
    check_path = check_path or load_checker(root)
    manifest = load_json(root / "retry_manifest_v20.json")
    jobs = manifest["jobs"]
    reviews = [parse_review(p) for p in sorted((root / "outputs").glob("*v20*.json.review3.md"))]
    review_by_candidate = {r["file"].removesuffix(".review3.md"): r for r in reviews}

    pass_rows = []
    structural_rows = []
    for model in MAIN_MODELS:
        initials = sorted((root / "outputs").glob(f"{model}_?_v20_*.json"))
        initials = [p for p in initials if "_r" not in p.stem]
        initial_valid = [p for p in initials if check_path(p)[0]]
        initial_judge_pass = sum(review_by_candidate.get(p.name, {}).get("verdict") == "过" for p in initial_valid)
        verdict_jobs = [j for j in jobs if j["model"] == model and j["group"] == "main_verdict"]
        structure_jobs = [j for j in jobs if j["model"] == model and j["group"] == "main_structure"]
        retry_stats = {}
        retry_pass_total = 0
        pending_reviews = 0
        for label, selected in (("verdict_retry", verdict_jobs), ("structure_retry", structure_jobs)):
            passed = completed = 0
            for job in selected:
                candidate, state = final_candidate(root, job, check_path)
                if not candidate:
                    continue
                review = review_by_candidate.get(candidate.name)
                if review:
                    completed += 1
                    passed += review["verdict"] == "过"
                else:
                    pending_reviews += 1
            retry_stats[label] = ratio(passed, len(selected)) | {"reviewed": completed}
            retry_pass_total += passed
        pass_rows.append({
            "model": model,
            "pass_at_1": ratio(initial_judge_pass, len(initials)),
            "verdict_retry": retry_stats["verdict_retry"],
            "structure_retry": retry_stats["structure_retry"],
            "pass_at_2": ratio(initial_judge_pass + retry_pass_total, len(initials)),
            "pending_retry_reviews": pending_reviews,
        })
        valid_with_retry = len(initial_valid)
        for job in structure_jobs:
            candidate, _ = final_candidate(root, job, check_path)
            valid_with_retry += candidate is not None
        structural_rows.append({"model": model, "first": ratio(len(initial_valid), len(initials)), "with_retry": ratio(valid_with_retry, len(initials))})

    # Multi-label cause counts: header counters and pass/fail fields each count independently.
    causes = {}
    inflation = {}
    diagnostics = {}
    for model in (*MAIN_MODELS, "ds_B_div"):
        selected = [r for r in reviews if r["model"] == model]
        counter = Counter()
        original = effective = 0
        hard10 = overreach = warnings = 0
        candidate_count = 0
        for r in selected:
            for key in ("死规则", "坍缩", "真空", "伪猜", "占优"):
                try:
                    counter[key] += int(r["fields"].get(key, "0"))
                except ValueError:
                    pass
            for key in ("终局", "结算", "落点", "融合"):
                counter[key] += r["fields"].get(key) == "破"
            counter["结算/终局/平局类"] += any(r["fields"].get(k) == "破" for k in ("终局", "结算")) or bool(re.search(r"平局.{0,30}(缺|未|破|随机)", r["items"].get(6, "") + r["items"].get(7, "")))
            if r["inflation"]:
                original += r["inflation"]["original"]
                effective += r["inflation"]["effective"]
            hard10 += r["fields"].get("小局身份") == "破" or bool(re.search(r"硬否决.{0,20}(触发|判破)", r["items"].get(10, "")))
            item8 = r["items"].get(8, "")
            overreach += bool(re.search(r"现实物品|沙漏|手机|纸条|纸笔|卡牌|记分板|投票器|转盘", item8) and re.search(r"盲区|僭越|系统|原语|落点", item8))
            candidate = root / "outputs" / r["file"].removesuffix(".review3.md")
            if candidate.exists():
                ok, ws = check_path(candidate)
                if ok:
                    candidate_count += 1
                    warnings += len(ws)
        causes[model] = dict(counter) | {"破因总数": sum(v for k, v in counter.items() if k != "结算/终局/平局类"), "Sonnet破因总数是否≥6": (sum(v for k, v in counter.items() if k != "结算/终局/平局类") >= 6) if model == "sonnet" else None}
        inflation[model] = {"original_y": original, "effective_x": effective, "density_x_over_y": round(effective / original, 6) if original else None}
        diagnostics[model] = {"item10_hard_veto": hard10, "item8_real_item_overreach": overreach, "warnings": warnings, "reviewed_valid_candidates": candidate_count, "warnings_per_candidate": round(warnings / candidate_count, 6) if candidate_count else None}

    b_jobs = [j for j in jobs if j["group"] == "b_structure"]
    b_job_by_source = {j["source"]: j for j in b_jobs}
    b_rows, b_pending = [], []
    for n in range(1, 21):
        source = f"ds_B_div_v20_{n:02d}.json"
        base = root / "outputs" / source
        if check_path(base)[0]:
            final, state = base, "initial_valid"
        else:
            final, state = final_candidate(root, b_job_by_source[source], check_path) if source in b_job_by_source else (None, "missing_job")
        if not final:
            b_pending.append({"source": source, "state": state})
            b_rows.append({"source": source, "final": None, "state": state, "shape": None, "loop": None, "canonical_family": None, "review": "pending"})
            continue
        doc = load_json(final)
        loop = core_loop(doc)
        review = review_by_candidate.get(final.name)
        shape = review["fields"].get("shape") if review else None
        b_rows.append({"source": source, "final": final.name, "state": state, "shape": shape, "loop": loop, "canonical_family": canonical_family(loop + json.dumps(doc, ensure_ascii=False)), "review": review["verdict"] if review else "pending"})

    usage = usage_rows(root / "usage_log.csv")
    usage_summary = {}
    for model in sorted({row.get("model", "") for row in usage}):
        selected = [r for r in usage if r.get("model") == model]
        def total(field):
            return sum(float(r.get(field) or 0) for r in selected)
        usage_summary[model] = {"calls": len(selected), "success": sum(r.get("status") == "success" for r in selected), "prompt_tokens": int(total("prompt_token")), "completion_tokens": int(total("completion_token")), "reasoning_tokens": int(total("reasoning_token")), "latency_seconds": round(total("latency_seconds"), 6), "cost_usd": round(total("cost_usd"), 9)}

    job_states = Counter()
    ds_retry_passes = []
    for job in jobs:
        candidate, state = final_candidate(root, job, check_path)
        review = review_by_candidate.get(candidate.name) if candidate else None
        if job["model"] == "dsT" and review and review["verdict"] == "过":
            ds_retry_passes.append(candidate.name)
        if candidate and not review_for(candidate).exists() and (job["group"] == "main_verdict" or job["group"] == "main_structure" or job["group"] == "b_structure"):
            state = "valid_pending_review"
        job_states[state] += 1
    web_jobs = [j for j in jobs if j.get("channel") == "web"]
    web_generated = sum(any(p.exists() for p in retry_paths(root, j)) for j in web_jobs)
    catalog_path = next((root / "docs").glob("36*.md"), None) if (root / "docs").exists() else None
    catalog = canonical_catalog(catalog_path) if catalog_path else []
    return {
        "version": manifest.get("version"), "status": dict(job_states),
        "pass": pass_rows, "structure": structural_rows, "reject_causes": causes,
        "inflation": inflation, "diagnostics": diagnostics,
        "b20": b_rows, "b20_pending": b_pending,
        "baseline_normalized": baseline_rows(root / "ds_B_div_shapes.md"),
        "canonical_catalog": {"source": catalog_path.name if catalog_path else None, "family_count": len(catalog), "families": catalog},
        "usage": usage_summary,
        "usage_tracking": {"web_jobs": len(web_jobs), "web_jobs_with_output": web_generated, "web_latency_tokens_tracked": False},
        "ds_retry_passes": ds_retry_passes,
        "notes": ["pass@2 = 首发裁判过 + 各件一次逻辑重试后的裁判过；缺 review 不计过并显式 pending。", "破因按 review header 多标签计数；结算/终局/平局类为独立布尔并集列。", "家族正典映射为按核心循环的显式人工规则；不能稳定归类者记为“边界件·待人工复核”。"]
    }


def md_ratio(x):
    return f"{x['passed']}/{x['total']}" + (f" ({x['rate']:.1%})" if x["rate"] is not None else "")


def render_markdown(data: dict) -> str:
    out = ["# v2.0 重试批出表包", "", f"状态：`{json.dumps(data['status'], ensure_ascii=False)}`", ""]
    out += ["## 1. pass 与结构", "", "| 模型 | pass@1 | 判破重试过 | 结构重试过 | pass@2 | 待裁判 | 结构首发 | 结构含重试 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    structures = {x["model"]: x for x in data["structure"]}
    for row in data["pass"]:
        s = structures[row["model"]]
        out.append(f"| {row['model']} | {md_ratio(row['pass_at_1'])} | {md_ratio(row['verdict_retry'])} | {md_ratio(row['structure_retry'])} | {md_ratio(row['pass_at_2'])} | {row['pending_retry_reviews']} | {md_ratio(s['first'])} | {md_ratio(s['with_retry'])} |")
    out += ["", f"DS 重试裁判过件：{len(data['ds_retry_passes'])}；" + ("、".join(data["ds_retry_passes"]) if data["ds_retry_passes"] else "无")]
    out += ["", "## 2. 破因分布（多标签）", "", "| 模型 | 死规则 | 坍缩 | 真空 | 伪猜 | 占优 | 终局 | 结算 | 落点 | 融合 | 结算/终局/平局类 | 破因总数 | Sonnet ≥6 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for m, c in data["reject_causes"].items():
        out.append("| " + " | ".join([m] + [str(c.get(k, 0)) for k in ("死规则", "坍缩", "真空", "伪猜", "占优", "终局", "结算", "落点", "融合", "结算/终局/平局类", "破因总数")] + ["是" if c.get("Sonnet破因总数是否≥6") else ("否" if m == "sonnet" else "—")]) + " |")
    out += ["", "## 3–4. 核心循环密度与诊断", "", "| 模型 | Σ原Y | Σ有效X | X/Y | 第10硬否决 | 第8现实物品僭越 | warning | warning/有效件 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m in data["inflation"]:
        i, d = data["inflation"][m], data["diagnostics"][m]
        out.append(f"| {m} | {i['original_y']} | {i['effective_x']} | {i['density_x_over_y'] if i['density_x_over_y'] is not None else '—'} | {d['item10_hard_veto']} | {d['item8_real_item_overreach']} | {d['warnings']} | {d['warnings_per_candidate'] if d['warnings_per_candidate'] is not None else '—'} |")
    out += ["", "## 5. B×20 终件同尺表", "", "| # | 终件 | 状态 | shape | 一句循环 | 正典家族 | review |", "|---:|---|---|---|---|---|---|"]
    for n, r in enumerate(data["b20"], 1):
        out.append(f"| {n} | {r['final'] or '—'} | {r['state']} | {r['shape'] or '—'} | {(r['loop'] or '—').replace('|','/')} | {r['canonical_family'] or '—'} | {r['review']} |")
    out += ["", "### 旧基线归一", "", "| id | shape | 一句循环 | 原自由文本家族 | 正典家族 |", "|---|---|---|---|---|"]
    for r in data["baseline_normalized"]:
        out.append(f"| {r['id']} | {r['shape']} | {r['loop'].replace('|','/')} | {r['raw_family']} | {r['canonical_family']} |")
    out += ["", "## 6. usage_log 汇总", "", "| API 模型 | 调用 | 成功 | prompt | completion | reasoning | latency(s) | cost(USD) |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for m, u in data["usage"].items():
        out.append(f"| {m} | {u['calls']} | {u['success']} | {u['prompt_tokens']} | {u['completion_tokens']} | {u['reasoning_tokens']} | {u['latency_seconds']} | {u['cost_usd']:.9f} |")
    tracking = data["usage_tracking"]
    out += ["", f"网页通道：任务 {tracking['web_jobs']}，已有输出 {tracking['web_jobs_with_output']}；latency/token 记录：否。"]
    out += ["", "口径："] + [f"- {n}" for n in data["notes"]] + [""]
    return "\n".join(out)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--markdown", type=Path, default=Path("v20_retry_report.md"))
    parser.add_argument("--json", type=Path, default=Path("v20_retry_report.json"))
    args = parser.parse_args(argv)
    data = build_data(args.root.resolve())
    md_path = args.markdown if args.markdown.is_absolute() else args.root / args.markdown
    json_path = args.json if args.json.is_absolute() else args.root / args.json
    md_path.write_text(render_markdown(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {md_path} and {json_path}; status={json.dumps(data['status'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
