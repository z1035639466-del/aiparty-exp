#!/usr/bin/env python3
"""Build the frozen DM-skill v2.1.1 r2 retry and result tables.

The builder reads candidates, warning sidecars, judge files, receipts and usage
logs.  It never rewrites a candidate, warning sidecar or review.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
MODEL_ORDER = ("sonnet", "haiku", "dsT", "sonL")
MODEL_LABEL = {
    "sonnet": "Sonnet 5",
    "haiku": "Haiku 4.5",
    "dsT": "dsT",
    "sonL": "Sonnet 5 Low (B×20)",
}
BASELINE_V20 = {"sonnet": 5, "haiku": 2, "dsT": 2, "sonL": None}
BASELINE_R1 = {"sonnet": 0, "haiku": 0, "dsT": 0, "sonL": 0}

NO_CHANNEL = {
    "sonnet_A_v21r2_01.json", "sonnet_A_v21r2_02.json",
    "sonnet_B_v21r2_01.json", "sonnet_C_v21r2_01.json",
    "sonnet_C_v21r2_02.json", "sonnet_D_v21r2_01.json",
    "sonnet_D_v21r2_02.json", "haiku_A_v21r2_01.json",
    "haiku_B_v21r2_02.json",
    "sonL_B_div_v21r2_01.json", "sonL_B_div_v21r2_02.json",
    "sonL_B_div_v21r2_03.json", "sonL_B_div_v21r2_04.json",
    "sonL_B_div_v21r2_07.json", "sonL_B_div_v21r2_08.json",
    "sonL_B_div_v21r2_09.json", "sonL_B_div_v21r2_11.json",
    "sonL_B_div_v21r2_14.json", "sonL_B_div_v21r2_18.json",
    "sonL_B_div_v21r2_19.json", "sonL_B_div_v21r2_20.json",
    "sonnet_A_v21r2_01_r1.json", "sonnet_B_v21r2_01_r1.json",
    "sonnet_B_v21r2_02_r1.json", "haiku_D_v21r2_01_r1.json",
    "dsT_C_v21r2_01_r1.json", "sonL_B_div_v21r2_13_r1.json",
}
# Existing v2.0 metric: unsubmitted physical/oral/intention/shared-memory fact
# treated as a machine fact.  Pure internal answer-map loss is not overreach.
OVERREACH = NO_CHANNEL - {
    "sonL_B_div_v21r2_08.json",
    "haiku_D_v21r2_01_r1.json",
}

FAMILY_BY_B_INDEX = {
    1: 41, 2: 41, 3: 7, 4: 41, 6: 40, 7: 4, 8: 7, 9: 41,
    10: 7, 11: 40, 13: 41, 14: 7, 17: 8, 18: 8, 19: 6, 20: 40,
}
FAMILY_EXPANSION = {
    37: "合作沟通拆弹",
    38: "集体去重提示",
    39: "光谱定位共情",
    40: "押注同桌答案",
    41: "随时喊停胆小鬼",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ratio(passed: int, total: int) -> dict[str, Any]:
    return {
        "passed": passed,
        "total": total,
        "rate": round(passed / total, 6) if total else None,
    }


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def ratio_md(value: dict[str, Any]) -> str:
    rate = value["rate"]
    suffix = "" if rate is None else f" ({rate:.1%})"
    return f"{value['passed']}/{value['total']}{suffix}"


def normalized_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def load_checker():
    spec = importlib.util.spec_from_file_location("frozen_check_v211", ROOT / "check.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module, module.load_whitelist()


CHECK_MODULE, WHITELIST = load_checker()


def check_path(path: Path) -> dict[str, Any]:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "errors": [f"JSON 不可解析: {exc}"], "warnings": []}
    result = CHECK_MODULE.check_document(data, WHITELIST)
    return {
        "passed": not result.errors,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }


def review_path(candidate: Path) -> Path:
    return candidate.with_name(candidate.name + ".review3.md")


def warning_path(candidate: Path) -> Path:
    return candidate.with_suffix(".warnings.json")


def parse_review(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    header = text.splitlines()[0]
    verdict_match = re.match(r"^VERDICT:\s*([^|]+)", header)
    fields: dict[str, Any] = {}
    for key in ("死规则", "坍缩", "真空", "伪猜", "占优"):
        match = re.search(rf"{key}:(\d+)", header)
        fields[key] = int(match.group(1)) if match else 0
    for key in ("终局", "结算", "落点", "融合", "小局身份"):
        match = re.search(rf"{key}:([^ |]+)", header)
        fields[key] = match.group(1) if match else "缺失"
    shape = re.search(r"shape:([^ |]+)", header)
    inflation = re.search(r"膨胀:原(\d+)条→有效(\d+)", header)
    candidate = path.name.removesuffix(".review3.md")
    return {
        "file": path.name,
        "candidate": candidate,
        "verdict": verdict_match.group(1).strip() if verdict_match else "缺失",
        "fields": fields,
        "shape": shape.group(1) if shape else None,
        "original_y": int(inflation.group(1)) if inflation else 0,
        "effective_x": int(inflation.group(2)) if inflation else 0,
    }


def initial_paths(model: str) -> list[Path]:
    if model == "sonL":
        return [OUTPUTS / f"sonL_B_div_v21r2_{n:02d}.json" for n in range(1, 21)]
    return [
        OUTPUTS / f"{model}_{letter}_v21r2_{n:02d}.json"
        for letter in "ABCD" for n in (1, 2)
    ]


def retry_paths(job: dict[str, Any]) -> list[Path]:
    paths = [OUTPUTS / job["target"]]
    if job.get("terminal_target"):
        paths.append(OUTPUTS / job["terminal_target"])
    return paths


def first_valid_retry(job: dict[str, Any]) -> Path | None:
    for path in retry_paths(job):
        if path.exists() and check_path(path)["passed"]:
            return path
    return None


def model_of(path: Path) -> str:
    if path.name.startswith("sonL_"):
        return "sonL"
    return path.name.split("_", 1)[0]


def build_family_catalog() -> list[dict[str, Any]]:
    path = ROOT / "docs" / "36家族机制三型归属表.md"
    families: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            families[int(match.group(1))] = match.group(2).replace("**", "").strip()
    families.update(FAMILY_EXPANSION)
    assert sorted(families) == list(range(1, 42)), sorted(families)
    return [{"id": n, "name": families[n]} for n in range(1, 42)]


def terminal_b_rows(
    b_jobs: dict[str, dict[str, Any]], reviews: dict[str, dict[str, Any]],
    families: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    family_pieces: dict[int, list[str]] = {n: [] for n in range(1, 42)}
    for n, initial in enumerate(initial_paths("sonL"), 1):
        if check_path(initial)["passed"]:
            terminal, state = initial, "initial_valid"
        else:
            job = b_jobs[initial.name]
            terminal = first_valid_retry(job)
            state = "valid_r1" if terminal else "exhausted_invalid_r2"
            if terminal is None:
                terminal = retry_paths(job)[-1]
        checked = check_path(terminal)
        review = reviews.get(terminal.name)
        family_id = FAMILY_BY_B_INDEX.get(n) if checked["passed"] else None
        if family_id:
            family_pieces[family_id].append(terminal.name)
        rows.append({
            "index": n,
            "source": initial.name,
            "terminal": terminal.name,
            "state": state,
            "structure": "PASS" if checked["passed"] else "REJECT",
            "judge": review["verdict"] if review else None,
            "shape": review["shape"] if review else None,
            "family_id": family_id,
            "family": families.get(family_id) if family_id else None,
            "terminal_errors": checked["errors"],
        })
    valid = sum(row["structure"] == "PASS" for row in rows)
    frequency = []
    for family_id in range(1, 42):
        pieces = family_pieces[family_id]
        frequency.append({
            "family_id": family_id,
            "family": families[family_id],
            "count": len(pieces),
            "share_of_valid": round(len(pieces) / valid, 6) if valid else None,
            "pieces": pieces,
        })
    return rows, frequency


def aggregate_reviews(paths: Iterable[Path]) -> dict[str, Any]:
    parsed = [parse_review(path) for path in paths]
    counts = Counter()
    doubts = Counter()
    for review in parsed:
        for key in ("死规则", "坍缩", "真空", "伪猜", "占优"):
            counts[key] += review["fields"][key]
        for key in ("终局", "结算", "落点", "融合"):
            counts[key] += review["fields"][key] == "破"
            doubts[key] += review["fields"][key] == "疑"
    original = sum(review["original_y"] for review in parsed)
    effective = sum(review["effective_x"] for review in parsed)
    candidates = {review["candidate"] for review in parsed}
    return {
        "reviews": len(parsed),
        "judge_pass": sum(review["verdict"] == "过" for review in parsed),
        "judge_break": sum(review["verdict"] == "破" for review in parsed),
        "causes": dict(counts),
        "doubts": dict(doubts),
        "cause_labels_total": sum(counts.values()),
        "no_channel": ratio(len(candidates & NO_CHANNEL), len(parsed)),
        "overreach": ratio(len(candidates & OVERREACH), len(parsed)),
        "item10_hard_veto": sum(review["fields"]["小局身份"] == "破" for review in parsed),
        "inflation": {
            "original_y": original,
            "effective_x": effective,
            "x_per_review": round(effective / len(parsed), 6) if parsed else None,
            "x_over_y": round(effective / original, 6) if original else None,
        },
        "files": sorted(candidates),
    }


def prop_health(model: str) -> dict[str, Any]:
    whitelist_doc = load_json(ROOT / "whitelist.json")
    canonical = set(whitelist_doc["props"])
    referencable = {
        name for name, kind in whitelist_doc["prop_reference_types"].items()
        if kind == "可引用"
    }
    literal, canonical_use, dead, warning_types = Counter(), Counter(), Counter(), Counter()
    parsed_count = 0
    for path in initial_paths(model):
        try:
            doc = load_json(path)
        except json.JSONDecodeError:
            continue
        parsed_count += 1
        names = {
            item.get("prop").strip()
            for item in (doc.get("props_dealt") or [])
            if isinstance(item, dict) and isinstance(item.get("prop"), str)
            and item.get("prop").strip()
        }
        literal.update(names)
        canonical_use.update(name for name in names if name in canonical)
        sidecar = warning_path(path)
        if sidecar.exists():
            for warning in load_json(sidecar).get("warnings", []):
                warning_types[warning.split(":", 1)[0]] += 1
                if warning.startswith("dead_prop:"):
                    dead[warning.split(":", 1)[1]] += 1
    top4 = canonical_use.most_common(4)
    canonical_total = sum(canonical_use.values())
    literal_total = sum(literal.values())
    reference_total = sum(count for name, count in canonical_use.items() if name in referencable)
    dead_total = sum(dead.values())
    outside = {name: count for name, count in sorted(literal.items()) if name not in canonical}
    return {
        "designs": len(initial_paths(model)),
        "json_parsed": parsed_count,
        "canonical_coverage": len(canonical_use),
        "literal_coverage": len(literal),
        "canonical_occurrences": canonical_total,
        "literal_occurrences": literal_total,
        "top4": [{"prop": name, "count": count} for name, count in top4],
        "top4_count": sum(count for _, count in top4),
        "top4_share": round(sum(count for _, count in top4) / canonical_total, 6)
        if canonical_total else None,
        "top4_baseline": 0.787,
        "outside_library_unique": len(outside),
        "outside_library_occurrences": sum(outside.values()),
        "outside_library": outside,
        "referencable_occurrences": reference_total,
        "true_dead_prop": dead_total,
        "true_dead_prop_rate": round(dead_total / reference_total, 6)
        if reference_total else None,
        "dead_over_canonical": round(dead_total / canonical_total, 6)
        if canonical_total else None,
        "dead_over_literal": round(dead_total / literal_total, 6)
        if literal_total else None,
        "dead_prop_by_name": dict(dead),
        "warning_count": sum(warning_types.values()),
        "warnings_per_design": round(sum(warning_types.values()) / len(initial_paths(model)), 6),
        "warning_types": dict(warning_types),
    }


def usage_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    def total(field: str) -> float:
        return sum(float(row.get(field) or 0) for row in rows)
    prompt = int(total("prompt_token"))
    completion = int(total("completion_token"))
    return {
        "calls": len(rows),
        "success": sum(row.get("status") == "success" for row in rows),
        "failed": sum(row.get("status") != "success" for row in rows),
        "status": dict(Counter(row.get("status") for row in rows)),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens_subset_of_completion": int(total("reasoning_token")),
        "total_tokens_prompt_plus_completion": prompt + completion,
        "latency_seconds": round(total("latency_seconds"), 6),
        "mean_latency_seconds": round(total("latency_seconds") / len(rows), 6) if rows else None,
        "cost_usd": round(total("cost_usd"), 9),
    }


def build_usage() -> dict[str, Any]:
    with (ROOT / "usage_log.csv").open(encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    pattern = re.compile(r"^dsT_[A-D]_v21r2_(01|02)(?:_r1)?\.json$")
    rows = [row for row in all_rows if pattern.fullmatch(row.get("filename", ""))]
    initial = [row for row in rows if "_r1.json" not in row["filename"]]
    retry = [row for row in rows if "_r1.json" in row["filename"]]
    web = load_json(ROOT / "web_receipts_v21r2_retry.json")
    valid = web["valid_attempts"]
    excluded = web["excluded_attempts"]
    return {
        "api": {
            "initial": usage_summary(initial),
            "retry_r1": usage_summary(retry),
            "total": usage_summary(rows),
            "reasoning_not_double_counted": True,
        },
        "web": {
            "valid": web["valid_count"],
            "pending": web["pending_valid_web_job_count"],
            "excluded": web["excluded_count"],
            "valid_by_model": dict(Counter(item["model"] for item in valid)),
            "valid_by_effort": dict(Counter(item["effort"] for item in valid)),
            "excluded_by_effort": dict(Counter(item["effort"] for item in excluded)),
            "token_latency_cost": None,
            "canonical_sha256": web["canonical_sha256"],
            "segment_sha256": web["segment_sha256"],
            "machine_validation": web["machine_validation"],
        },
    }


def build_data() -> dict[str, Any]:
    assert CHECK_MODULE.SPEC_VERSION == "v2.1.1"
    manifest = load_json(ROOT / "retry_manifest_v21r2.json")
    jobs = manifest["jobs"]
    reviews = {
        path.name.removesuffix(".review3.md"): parse_review(path)
        for path in OUTPUTS.glob("*.json.review3.md")
    }
    families_list = build_family_catalog()
    families = {row["id"]: row["name"] for row in families_list}
    b_jobs = {job["source"]: job for job in jobs if job["group"] == "b_structure"}
    b_rows, family_frequency = terminal_b_rows(b_jobs, reviews, families)

    pass_rows, structure_rows = [], []
    scopes: dict[str, dict[str, list[Path]]] = {
        scope: {model: [] for model in MODEL_ORDER}
        for scope in ("first_pass", "retry", "terminal")
    }
    for model in MODEL_ORDER:
        initials = initial_paths(model)
        initial_valid = [path for path in initials if check_path(path)["passed"]]
        initial_reviewed = [path for path in initial_valid if path.name in reviews]
        initial_pass = sum(reviews[path.name]["verdict"] == "过" for path in initial_reviewed)
        scopes["first_pass"][model] = [review_path(path) for path in initial_reviewed]
        model_jobs = [job for job in jobs if job["model"] == model]

        retry_group: dict[str, dict[str, Any]] = {}
        retry_judge_pass = 0
        for group in ("main_verdict", "main_structure", "b_structure"):
            selected = [job for job in model_jobs if job["group"] == group]
            r1_pass = r2_pass = reviewed = judged_pass = attempts = 0
            for job in selected:
                paths = retry_paths(job)
                attempts += sum(path.exists() for path in paths)
                if paths[0].exists() and check_path(paths[0])["passed"]:
                    r1_pass += 1
                if len(paths) > 1 and paths[1].exists() and check_path(paths[1])["passed"]:
                    r2_pass += 1
                candidate = first_valid_retry(job)
                if candidate and candidate.name in reviews:
                    reviewed += 1
                    judged_pass += reviews[candidate.name]["verdict"] == "过"
                    scopes["retry"][model].append(review_path(candidate))
            retry_group[group] = {
                "jobs": len(selected), "attempts_present": attempts,
                "r1_structure_pass": r1_pass, "r2_additional_structure_pass": r2_pass,
                "reviewed": reviewed, "judge_pass": judged_pass,
                "exhausted_invalid": len(selected) - (r1_pass + r2_pass),
            }
            retry_judge_pass += judged_pass

        if model == "sonL":
            terminal_candidates = [
                OUTPUTS / row["terminal"] for row in b_rows if row["structure"] == "PASS"
            ]
        else:
            terminal_candidates = [
                OUTPUTS / job["target"] for job in model_jobs
                if (OUTPUTS / job["target"]).exists()
                and check_path(OUTPUTS / job["target"])["passed"]
            ]
        scopes["terminal"][model] = [
            review_path(path) for path in terminal_candidates if path.name in reviews
        ]
        structure_group = "b_structure" if model == "sonL" else "main_structure"
        restored = retry_group[structure_group]["r1_structure_pass"] + retry_group[structure_group]["r2_additional_structure_pass"]
        pass_rows.append({
            "model": MODEL_LABEL[model],
            "key": model,
            "pass_at_1": ratio(initial_pass, len(initials)),
            "verdict_retry": retry_group["main_verdict"],
            "structure_retry": retry_group[structure_group],
            "pass_at_2": ratio(initial_pass + retry_judge_pass, len(initials)),
        })
        structure_rows.append({
            "model": MODEL_LABEL[model],
            "key": model,
            "first_pass": ratio(len(initial_valid), len(initials)),
            "after_structure_repair": ratio(len(initial_valid) + restored, len(initials)),
            "terminal_artifact": ratio(len(terminal_candidates), len(initials)),
            "warning_count_first_pass": sum(len(check_path(path)["warnings"]) for path in initials),
            "warnings_per_design": round(sum(len(check_path(path)["warnings"]) for path in initials) / len(initials), 6),
        })

    cause_tables = {
        scope: {
            model: aggregate_reviews(scopes[scope][model]) for model in MODEL_ORDER
        }
        for scope in scopes
    }

    all_candidates = []
    for model in MODEL_ORDER:
        all_candidates.extend(initial_paths(model))
    probes = [OUTPUTS / f"sonnet_A2_probe_{n:02d}.json" for n in (1, 2)]
    all_candidates.extend(probes)
    for job in jobs:
        all_candidates.extend(retry_paths(job))
    all_candidates = sorted({path for path in all_candidates if path.exists()})
    sidecar_mismatches = []
    invalid_review_files = []
    for candidate in all_candidates:
        checked = check_path(candidate)
        sidecar = warning_path(candidate)
        if checked["warnings"]:
            if not sidecar.exists():
                sidecar_mismatches.append(f"missing:{candidate.name}")
            else:
                payload = load_json(sidecar)
                if payload.get("spec_version") != "v2.1.1" or payload.get("warnings") != checked["warnings"]:
                    sidecar_mismatches.append(f"content:{candidate.name}")
        elif sidecar.exists():
            sidecar_mismatches.append(f"unexpected:{candidate.name}")
        if not checked["passed"] and review_path(candidate).exists():
            invalid_review_files.append(review_path(candidate).name)
    assert not sidecar_mismatches, sidecar_mismatches
    assert not invalid_review_files, invalid_review_files

    compiler = load_json(ROOT / "compiler_probe_v21r2.json")
    judged_pass_files = sorted({
        candidate for candidate, review in reviews.items()
        if "v21r2" in candidate and review["verdict"] == "过"
    })
    compiler_files = sorted(row["file"] for row in compiler["candidates"])
    assert compiler_files == judged_pass_files, (compiler_files, judged_pass_files)

    probe_rows = []
    for path in probes:
        doc = load_json(path)
        props = doc.get("props_required") or []
        probe_rows.append({
            "file": path.name,
            "structure": "PASS" if check_path(path)["passed"] else "REJECT",
            "props_required": props,
            "inferred_empty_bottle": any(
                isinstance(value, str) and "空酒瓶" in value and "推得" in value
                for value in props
            ),
            "excluded_from_main_denominators": True,
        })

    structure_by_key = {row["key"]: row for row in structure_rows}
    treatment = []
    for model in MODEL_ORDER:
        row = structure_by_key[model]
        current = row["first_pass"]["passed"]
        total = row["first_pass"]["total"]
        baseline_v20 = BASELINE_V20[model]
        treatment.append({
            "model": MODEL_LABEL[model], "key": model,
            "v20": ratio(baseline_v20, total) if baseline_v20 is not None else None,
            "r1_v21": ratio(BASELINE_R1[model], total),
            "r2_v211": ratio(current, total),
            "r2_minus_r1_percentage_points": round((current - BASELINE_R1[model]) / total * 100, 1),
            "only_variable": "教材 v2.1→v2.1.1",
        })

    shape_counts = Counter(row["shape"] for row in b_rows if row["shape"])
    nonzero_families = [row for row in family_frequency if row["count"]]
    top_family_counts = sorted((row["count"] for row in nonzero_families), reverse=True)
    b_valid = sum(row["structure"] == "PASS" for row in b_rows)
    b_summary = {
        "initial_structure": ratio(sum(row["state"] == "initial_valid" for row in b_rows), 20),
        "terminal_structure": ratio(b_valid, 20),
        "r1_restored": sum(row["state"] == "valid_r1" for row in b_rows),
        "r2_additional_restored": 0,
        "exhausted_invalid": sum(row["structure"] == "REJECT" for row in b_rows),
        "shape_frequency": dict(shape_counts),
        "largest_shape_share": round(max(shape_counts.values()) / b_valid, 6),
        "family_coverage": len(nonzero_families),
        "top1_family_share": round(top_family_counts[0] / b_valid, 6),
        "top2_family_share": round(sum(top_family_counts[:2]) / b_valid, 6),
        "triggers": {
            "single_shape_ge_40pct": max(shape_counts.values()) / b_valid >= 0.40,
            "top2_families_ge_50pct": sum(top_family_counts[:2]) / b_valid >= 0.50,
            "family_coverage_lt_6": len(nonzero_families) < 6,
            "套皮未解": True,
        },
        "dual_confound": "skill v1.8→v2.1.1 × 生成模型 DS→Sonnet 5 Low；Low 与主批 Sonnet 默认档亦异",
    }

    data = {
        "header": {
            "target_conversation": "DM-skill v2.1.1 验证批 r2",
            "repository": "aiparty-exp",
            "data_head": git_head(),
        },
        "versions": {
            "skill": {"version": "v2.1.1", "normalized_sha256": normalized_sha256(ROOT / "docs/specs/DM-skill-v2.1.1.md")},
            "check": {"SPEC_VERSION": CHECK_MODULE.SPEC_VERSION, "normalized_sha256": normalized_sha256(ROOT / "check.py")},
            "judge": {"version": "v0.3", "normalized_sha256": normalized_sha256(ROOT / "docs/specs/spec-judge-v0.3.md")},
            "whitelist": {"schema_version": load_json(ROOT / "whitelist.json")["schema_version"], "normalized_sha256": normalized_sha256(ROOT / "whitelist.json")},
            "golden": {letter: normalized_sha256(ROOT / f"inputs/input_{letter}.json") for letter in "ABCD"},
        },
        "population": {
            "main_initial": 24, "b20_initial": 20, "main_denominator": 44,
            "probe": 2, "retry_jobs": len(jobs),
            "retry_attempts_present": sum(len(retry_paths(job)) for job in jobs),
        },
        "pass": pass_rows,
        "structure": structure_rows,
        "textbook_effect": treatment,
        "review_metrics": cause_tables,
        "prop_health_first_pass": {model: prop_health(model) for model in MODEL_ORDER},
        "b20": {"summary": b_summary, "terminal_rows": b_rows, "family_frequency_41": family_frequency},
        "probe_A_prime": {
            "question": "props_required 有无‘推得’标注的空酒瓶",
            "positive": sum(row["inferred_empty_bottle"] for row in probe_rows),
            "total": len(probe_rows), "rows": probe_rows,
        },
        "compiler": compiler,
        "usage": build_usage(),
        "protocol_audit": {
            "candidate_json": len(all_candidates),
            "warning_sidecars": sum(warning_path(path).exists() for path in all_candidates),
            "sidecar_mismatch": len(sidecar_mismatches),
            "invalid_candidate_reviews": len(invalid_review_files),
            "main_first_pass_structure_pass_and_reviewed": sum(cause_tables["first_pass"][m]["reviews"] for m in MODEL_ORDER),
            "retry_structure_pass_and_reviewed": sum(cause_tables["retry"][m]["reviews"] for m in MODEL_ORDER),
            "terminal_structure_pass_and_reviewed": sum(cause_tables["terminal"][m]["reviews"] for m in MODEL_ORDER),
            "terminal_structure_reject_no_judgment": 44 - sum(cause_tables["terminal"][m]["reviews"] for m in MODEL_ORDER),
        },
        "thresholds": {
            "semantic": {"sonnet_pass_at_1": pass_rows[0]["pass_at_1"], "baseline_rate": 0.125, "green": False},
            "teaching": {"sonnet_rate": structure_rows[0]["first_pass"]["rate"], "explanation_release_rate": 0.90, "green": False},
            "prop_health": {"record_only": True, "top4_baseline": 0.787},
            "switch": {"sonnet_judge_rate": pass_rows[0]["pass_at_1"]["rate"], "required": 0.30, "compiler_regression": "16/16", "green": False},
            "first_three_green": {"file": compiler["first_three_green"], "green": True},
        },
        "family_catalog": {"count": len(families_list), "families": families_list},
        "notes": {
            "pass_at_2": "首发判过 + 允许重试后判过；结构拒件不判级",
            "reasoning_tokens": "completion 子集，不重复加总",
            "terminal_scope": "主批以 _r1 为终件；B×20 首个结构有效件，否则 _r2 耗尽",
        },
    }
    return data


def render_markdown(data: dict[str, Any]) -> str:
    out = [
        f"目标对话：{data['header']['target_conversation']}",
        f"仓名：{data['header']['repository']}",
        f"HEAD：{data['header']['data_head']}", "", "# v2.1.1 r2 重试与出表包", "",
        "## 1. pass@1 / pass@2 与两类重试", "",
        "| 模型 | pass@1 | 判破重试 jobs/闸过/裁过 | 结构重试 jobs/恢复/裁过 | pass@2 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in data["pass"]:
        vr, sr = row["verdict_retry"], row["structure_retry"]
        out.append(
            f"| {row['model']} | {ratio_md(row['pass_at_1'])} | "
            f"{vr['jobs']}/{vr['r1_structure_pass'] + vr['r2_additional_structure_pass']}/{vr['judge_pass']} | "
            f"{sr['jobs']}/{sr['r1_structure_pass'] + sr['r2_additional_structure_pass']}/{sr['judge_pass']} | "
            f"{ratio_md(row['pass_at_2'])} |"
        )
    out += ["", "## 2. params 首过与教材疗效", "",
            "| 模型 | v2.0 | r1(v2.1) | r2(v2.1.1) | r2-r1 | 结构修复后 | 终件过闸 | warning/件 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    structures = {row["key"]: row for row in data["structure"]}
    for row in data["textbook_effect"]:
        s = structures[row["key"]]
        out.append(
            f"| {row['model']} | {ratio_md(row['v20']) if row['v20'] else 'N/A'} | "
            f"{ratio_md(row['r1_v21'])} | {ratio_md(row['r2_v211'])} | "
            f"{row['r2_minus_r1_percentage_points']:+.1f}pp | {ratio_md(s['after_structure_repair'])} | "
            f"{ratio_md(s['terminal_artifact'])} | {s['warnings_per_design']:.3f} |"
        )

    for scope, title in (("first_pass", "首发"), ("retry", "重试"), ("terminal", "终件")):
        out += ["", f"## 3.{ {'first_pass':'1','retry':'2','terminal':'3'}[scope] } 破因·密度·第10项·僭越（{title}）", "",
                "| 模型 | review 过/破 | 死 | 坍 | 真空 | 伪猜 | 占优 | 终局 | 结算 | 落点 | 融合 | 无通道 | 僭越 | Y→X | X/件 | X/Y | 第10项 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for model in MODEL_ORDER:
            row = data["review_metrics"][scope][model]
            c, inf = row["causes"], row["inflation"]
            out.append(
                f"| {MODEL_LABEL[model]} | {row['reviews']} {row['judge_pass']}/{row['judge_break']} | "
                + " | ".join(str(c.get(k, 0)) for k in ("死规则", "坍缩", "真空", "伪猜", "占优", "终局", "结算", "落点", "融合"))
                + f" | {ratio_md(row['no_channel'])} | {ratio_md(row['overreach'])} | "
                f"{inf['original_y']}→{inf['effective_x']} | "
                f"{inf['x_per_review'] if inf['x_per_review'] is not None else 'N/A'} | "
                f"{percent(inf['x_over_y'])} | {row['item10_hard_veto']} |"
            )

    out += ["", "## 4. 看点六（首发旁车口径）", "",
            "| 模型 | 正典覆盖/字面覆盖 | 前四/正典下发 | 对78.7% | 库外 unique/occ | 真 dead/ref | dead/字面下发 | warning/件 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model in MODEL_ORDER:
        row = data["prop_health_first_pass"][model]
        out.append(
            f"| {MODEL_LABEL[model]} | {row['canonical_coverage']}/{row['literal_coverage']} | "
            f"{row['top4_count']}/{row['canonical_occurrences']} ({percent(row['top4_share'])}) | "
            f"{(row['top4_share'] - 0.787) * 100:+.1f}pp | "
            f"{row['outside_library_unique']}/{row['outside_library_occurrences']} | "
            f"{row['true_dead_prop']}/{row['referencable_occurrences']} ({percent(row['true_dead_prop_rate'])}) | "
            f"{row['true_dead_prop']}/{row['literal_occurrences']} ({percent(row['dead_over_literal'])}) | "
            f"{row['warning_count']}/{row['designs']} ({row['warnings_per_design']:.3f}) |"
        )

    b = data["b20"]
    out += ["", "## 5. B×20 终件", "",
            f"结构：{ratio_md(b['summary']['initial_structure'])} → {ratio_md(b['summary']['terminal_structure'])}；"
            f"r1 恢复 {b['summary']['r1_restored']}；r2 新增 {b['summary']['r2_additional_restored']}；"
            f"耗尽 {b['summary']['exhausted_invalid']}。", "",
            "| # | 终件 | 闸 | 裁 | shape | 家族 |", "|---:|---|---|---|---|---|"]
    for row in b["terminal_rows"]:
        family = f"{row['family_id']} {row['family']}" if row["family_id"] else "—"
        out.append(f"| {row['index']:02d} | {row['terminal']} | {row['structure']} | {row['judge'] or '—'} | {row['shape'] or '—'} | {family} |")
    out += ["", "### 41 族频次", "", "| # | 家族 | 数 | 有效占比 |", "|---:|---|---:|---:|"]
    for row in b["family_frequency_41"]:
        out.append(f"| {row['family_id']} | {row['family']} | {row['count']} | {percent(row['share_of_valid'])} |")
    out += ["", f"shape 最大占比：{percent(b['summary']['largest_shape_share'])}；家族覆盖：{b['summary']['family_coverage']}/41；"
            f"前一族：{percent(b['summary']['top1_family_share'])}；前二族：{percent(b['summary']['top2_family_share'])}。",
            f"触发：shape={int(b['summary']['triggers']['single_shape_ge_40pct'])}；前二族={int(b['summary']['triggers']['top2_families_ge_50pct'])}；"
            f"覆盖<6={int(b['summary']['triggers']['family_coverage_lt_6'])}；套皮未解={int(b['summary']['triggers']['套皮未解'])}。",
            f"双混杂注：{b['summary']['dual_confound']}。"]

    compiler = data["compiler"]
    probe = data["probe_A_prime"]
    out += ["", "## 6. 三绿与 A′", "",
            f"三绿候选覆盖：{compiler['candidate_coverage']}；编译通过：1/1；finale：1/1；模板回归：16/16；typecheck：1/1。",
            f"首件三绿：`{compiler['first_three_green']}`。",
            f"A′ 空酒瓶‘推得’：{probe['positive']}/{probe['total']}（主表分母 0）。"]

    api, web = data["usage"]["api"], data["usage"]["web"]
    out += ["", "## 7. usage / 成本", "",
            "| API | calls | 成功/失败 | prompt | completion | reasoning⊂completion | total | latency总/均 | USD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, label in (("initial", "首发"), ("retry_r1", "r1"), ("total", "合计")):
        row = api[key]
        out.append(f"| {label} | {row['calls']} | {row['success']}/{row['failed']} | {row['prompt_tokens']} | {row['completion_tokens']} | {row['reasoning_tokens_subset_of_completion']} | {row['total_tokens_prompt_plus_completion']} | {row['latency_seconds']:.6f}/{row['mean_latency_seconds']:.6f} | ${row['cost_usd']:.9f} |")
    out += ["", f"网页：有效 {web['valid']}；pending {web['pending']}；排除 {web['excluded']}；Low 9；参数不可控 16；token/latency/cost=N/A。"]

    audit = data["protocol_audit"]
    th = data["thresholds"]
    out += ["", "## 8. 协议、冻结与五阈值", "",
            f"候选 JSON {audit['candidate_json']}；旁车 {audit['warning_sidecars']}；旁车差分 {audit['sidecar_mismatch']}；结构拒件误判决 {audit['invalid_candidate_reviews']}。",
            f"首发闸过/裁扫 {audit['main_first_pass_structure_pass_and_reviewed']}；重试闸过/裁扫 {audit['retry_structure_pass_and_reviewed']}；"
            f"终件闸过/裁扫 {audit['terminal_structure_pass_and_reviewed']}；终件拒件/无判决 {audit['terminal_structure_reject_no_judgment']}。",
            f"五阈值：语义绿={int(th['semantic']['green'])}；教学90线绿={int(th['teaching']['green'])}；看点六=记录；"
            f"切换双绿={int(th['switch']['green'])}；三绿首件={int(th['first_three_green']['green'])}。",
            f"冻结哈希：skill `{data['versions']['skill']['normalized_sha256']}`；check `{data['versions']['check']['normalized_sha256']}`；"
            f"judge `{data['versions']['judge']['normalized_sha256']}`；whitelist `{data['versions']['whitelist']['normalized_sha256']}`。"]
    return "\n".join(out) + "\n"


def main() -> int:
    data = build_data()
    (ROOT / "v21r2_retry_report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ROOT / "v21r2_retry_report.md").write_text(render_markdown(data), encoding="utf-8")
    print("wrote v21r2_retry_report.json and v21r2_retry_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
