"""Batch-generate game JSON with DeepSeek while enforcing a hard cost ceiling."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = ROOT / "outputs"
SYSTEM_PATH = ROOT / "DM-skill-开局生成-v2.0.md"
INPUTS_DIR = ROOT / "inputs"
USAGE_PATH = ROOT / "usage_log.csv"

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
TEMPERATURE = 0.8
DEFAULT_MAX_TOKENS = 16_000
DEFAULT_BUDGET = 5.0
MAX_RETRIES = 3
PROMPT_OVERHEAD_TOKENS = 1_024

# Official USD prices per 1M tokens. Input uses the conservative cache-miss price.
INPUT_PRICE = 0.14
OUTPUT_PRICE = 0.28

USAGE_FIELDS = [
    "filename",
    "model",
    "thinking",
    "reasoning_effort",
    "prompt_token",
    "completion_token",
    "reasoning_token",
    "latency_seconds",
    "cost_usd",
    "status",
]


@dataclass(frozen=True)
class Job:
    input_name: str
    filename: str
    thinking: bool
    reasoning_effort: str


@dataclass(frozen=True)
class JobResult:
    output_path: Path
    spent: float


@dataclass(frozen=True)
class UsageSummary:
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float
    average_latency: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BudgetExceeded(RuntimeError):
    pass


class JobFailed(RuntimeError):
    def __init__(self, message: str, spent: float):
        super().__init__(message)
        self.spent = spent


class TransportFailure(RuntimeError):
    def __init__(self, message: str, raw_body: str = ""):
        super().__init__(message)
        self.raw_body = raw_body or message


def build_jobs() -> list[Job]:
    jobs: list[Job] = []
    for name in "ABCD":
        for sequence in range(1, 3):
            jobs.append(Job(name, f"dsT_{name}_v20_{sequence:02d}.json", True, "high"))
    for sequence in range(1, 21):
        jobs.append(Job("B", f"ds_B_div_v20_{sequence:02d}.json", False, ""))
    return jobs


def select_jobs(thinking_mode: str) -> list[Job]:
    jobs = build_jobs()
    if thinking_mode == "off":
        return [job for job in jobs if not job.thinking]
    if thinking_mode == "on":
        return [job for job in jobs if job.thinking]
    if thinking_mode == "both":
        return jobs
    raise ValueError(f"未知 thinking 模式: {thinking_mode}")


def prompt_token_upper_bound(system_text: str, user_text: str = "") -> int:
    """Use UTF-8 bytes as a conservative upper bound for byte-based tokenization."""
    return len(system_text.encode("utf-8")) + len(user_text.encode("utf-8"))


def estimate_call_cost(prompt_text: str, max_tokens: int) -> float:
    prompt_ceiling = prompt_token_upper_bound(prompt_text) + PROMPT_OVERHEAD_TOKENS
    return (prompt_ceiling * INPUT_PRICE + max_tokens * OUTPUT_PRICE) / 1_000_000


def actual_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * INPUT_PRICE + completion_tokens * OUTPUT_PRICE) / 1_000_000


def require_call_budget(spent: float, call_ceiling: float, budget: float) -> None:
    if spent + call_ceiling > budget + 1e-12:
        raise BudgetExceeded(
            f"预算不足以启动完整一发：已用 ${spent:.6f}，"
            f"本发上界 ${call_ceiling:.6f}，硬顶 ${budget:.2f}"
        )


def make_payload(system_text: str, user_text: str, *, thinking: bool, max_tokens: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if thinking:
        payload["reasoning_effort"] = "high"
    return payload


def api_transport(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise TransportFailure(f"HTTP {error.code}", raw) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportFailure(f"网络错误: {error}") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise TransportFailure(f"API 响应不是 JSON: {error}", raw) from error


def attempt_path(outputs_dir: Path, filename: str, attempt: int) -> Path:
    base = Path(filename)
    suffix = "" if attempt == 0 else f"_r{attempt}"
    return outputs_dir / f"{base.stem}{suffix}{base.suffix}"


def extract_usage(response: dict[str, Any]) -> tuple[int, int, int]:
    usage = response.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
    return prompt, completion, reasoning


def extract_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"API 响应缺少 choices[0].message.content: {error}") from error
    if not isinstance(content, str):
        raise ValueError("API content 不是字符串")
    return content


def append_usage(path: Path, row: dict[str, Any]) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=USAGE_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def run_job(
    job: Job,
    *,
    root: Path,
    keys: list[str],
    transport: Callable[[str, dict[str, Any]], dict[str, Any]],
    max_tokens: int,
    budget: float,
    spent: float,
) -> JobResult:
    system_text = (root / SYSTEM_PATH.name).read_text(encoding="utf-8")
    user_text = (root / "inputs" / f"input_{job.input_name}.json").read_text(encoding="utf-8")
    outputs_dir = root / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    usage_path = root / "usage_log.csv"
    payload = make_payload(system_text, user_text, thinking=job.thinking, max_tokens=max_tokens)
    call_ceiling = estimate_call_cost(system_text + user_text, max_tokens)
    current_spent = spent

    for attempt in range(MAX_RETRIES + 1):
        require_call_budget(current_spent, call_ceiling, budget)
        path = attempt_path(outputs_dir, job.filename, attempt)
        started = time.perf_counter()
        prompt_tokens = completion_tokens = reasoning_tokens = 0
        status = "failed"
        raw_to_save = ""
        try:
            response = transport(keys[attempt % len(keys)], payload)
            prompt_tokens, completion_tokens, reasoning_tokens = extract_usage(response)
            content = extract_content(response)
            raw_to_save = content
            json.loads(content)
            status = "success"
        except TransportFailure as error:
            raw_to_save = error.raw_body
            status = f"transport_error: {error}"
        except (ValueError, json.JSONDecodeError) as error:
            if not raw_to_save:
                raw_to_save = json.dumps({"error": str(error)}, ensure_ascii=False, indent=2)
            status = f"invalid_json: {error}"
        latency = time.perf_counter() - started
        cost = actual_cost(prompt_tokens, completion_tokens)
        current_spent += cost
        path.write_text(raw_to_save, encoding="utf-8")
        append_usage(
            usage_path,
            {
                "filename": path.name,
                "model": MODEL,
                "thinking": str(job.thinking).lower(),
                "reasoning_effort": job.reasoning_effort,
                "prompt_token": prompt_tokens,
                "completion_token": completion_tokens,
                "reasoning_token": reasoning_tokens,
                "latency_seconds": f"{latency:.6f}",
                "cost_usd": f"{cost:.9f}",
                "status": status,
            },
        )
        if status == "success":
            return JobResult(path, current_spent)
    raise JobFailed(f"{job.filename} 在 {MAX_RETRIES + 1} 次尝试后仍失败", current_spent)


def load_keys(key_file: Path | None) -> list[str]:
    candidates = os.environ.get("DEEPSEEK_API_KEYS", "")
    if key_file:
        candidates += "\n" + key_file.read_text(encoding="utf-8-sig")
    keys = list(dict.fromkeys(re.findall(r"sk-[A-Za-z0-9_-]{20,}", candidates)))
    if not keys:
        raise ValueError("未找到 API key；设置 DEEPSEEK_API_KEYS 或传 --key-file")
    return keys


def read_usage_rows(path: Path = USAGE_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def successful_job_filenames(rows: Iterable[dict[str, str]]) -> set[str]:
    """Return logical job names for successful attempts, including successful retries."""
    successful: set[str] = set()
    for row in rows:
        if row.get("status") != "success":
            continue
        name = row.get("filename", "")
        successful.add(re.sub(r"_r\d+(?=\.json$)", "", name))
    return successful


def summarize_usage(rows: Iterable[dict[str, str]]) -> UsageSummary:
    materialized = list(rows)
    prompt = sum(int(row.get("prompt_token") or 0) for row in materialized)
    completion = sum(int(row.get("completion_token") or 0) for row in materialized)
    reasoning = sum(int(row.get("reasoning_token") or 0) for row in materialized)
    cost = sum(float(row.get("cost_usd") or 0) for row in materialized)
    latencies = [float(row.get("latency_seconds") or 0) for row in materialized]
    average = sum(latencies) / len(latencies) if latencies else 0.0
    return UsageSummary(prompt, completion, reasoning, cost, average)


def classify_check_line(line: str) -> str:
    if re.search(r"fable_[ABCD]_v11_01\.json", line):
        return "history_v11"
    if "v20" in line:
        return "v20"
    return "other"


def check_subprocess_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def validate_ds_files(paths: Iterable[Path]) -> bool:
    import check

    whitelist = check.load_whitelist()
    passed = True
    print("\n=== 本次 DS 输出单独校验 ===")
    for path in paths:
        result = check.check_file(path, whitelist)
        if result.errors:
            passed = False
            print(f"挂 {path.name}: {'；'.join(result.errors)}")
        else:
            print(f"过 {path.name}: 检查通过")
        if result.warnings:
            print(f"  ⚠ 软闸(件头) {path.name}: {'；'.join(result.warnings)}")
    return passed


def run_full_check(root: Path) -> int:
    result = subprocess.run(
        [sys.executable, str(root / "check.py")],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=check_subprocess_environment(),
        check=False,
    )
    groups = {"history_v11": [], "v20": [], "other": []}
    for line in result.stdout.splitlines():
        groups[classify_check_line(line)].append(line)
    print("\n=== v11 历史产物（不计入矩阵） ===")
    print("\n".join(groups["history_v11"]) or "无")
    print("\n=== v2.0 验证批及本次 DS 产物 ===")
    print("\n".join(groups["v20"]) or "无")
    print("\n=== 其他版本 ===")
    print("\n".join(groups["other"]) or "无")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def estimate_batches(
    root: Path, max_tokens: int, thinking_mode: str = "both"
) -> tuple[float, list[tuple[Job, float]]]:
    system_text = (root / SYSTEM_PATH.name).read_text(encoding="utf-8")
    estimates: list[tuple[Job, float]] = []
    for job in select_jobs(thinking_mode):
        user_text = (root / "inputs" / f"input_{job.input_name}.json").read_text(encoding="utf-8")
        estimates.append((job, estimate_call_cost(system_text + user_text, max_tokens)))
    return sum(cost for _, cost in estimates), estimates


def print_estimate(root: Path, max_tokens: int, budget: float, thinking_mode: str = "both") -> float:
    total, estimates = estimate_batches(root, max_tokens, thinking_mode)
    batches = [
        ("dsT v2.0 思考主批", [item for item in estimates if item[0].filename.startswith("dsT_")]),
        ("B v2.0 非思考多样性批", [item for item in estimates if "_div_" in item[0].filename]),
    ]
    print(f"模型: {MODEL}  max_tokens: {max_tokens}  硬顶: ${budget:.2f}")
    for label, items in batches:
        if items:
            print(f"{label}: {len(items)} 发，完整上界 ${sum(cost for _, cost in items):.6f}")
    print(f"所选批次合计（无重试）: ${total:.6f}")
    print(f"最坏重试上界（每发初次 + {MAX_RETRIES} 次重试）: ${total * (MAX_RETRIES + 1):.6f}")
    return total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="只打印成本预估（默认）")
    mode.add_argument("--run", action="store_true", help="执行三批付费调用")
    parser.add_argument("--yes", action="store_true", help="与 --run 同用，确认已审阅预估")
    parser.add_argument("--key-file", type=Path, help="从本地文本提取 API key；文件不会被修改")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET)
    parser.add_argument(
        "--thinking",
        choices=("off", "on", "both"),
        default="both",
        help="off=关闭思考矩阵+多样性，on=思考矩阵，both=全部三批",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_tokens <= 0 or args.budget <= 0:
        print("max_tokens 和 budget 必须大于 0", file=sys.stderr)
        return 2
    print_estimate(ROOT, args.max_tokens, args.budget, args.thinking)
    if not args.run:
        print("预估模式：未发送任何 API 请求。确认后使用 --run --yes。")
        return 0
    if not args.yes:
        print("拒绝执行：付费调用还需要显式 --yes。", file=sys.stderr)
        return 2

    try:
        keys = load_keys(args.key_file)
    except (OSError, ValueError) as error:
        print(f"密钥加载失败: {error}", file=sys.stderr)
        return 2

    existing_rows = read_usage_rows()
    spent = sum(float(row.get("cost_usd") or 0) for row in existing_rows)
    completed: list[Path] = []
    failures = 0
    try:
        completed_names = successful_job_filenames(existing_rows)
        jobs = [job for job in select_jobs(args.thinking) if job.filename not in completed_names]
        if completed_names:
            print(f"恢复执行：跳过 {len(completed_names)} 个已成功文件。")
        for index, job in enumerate(jobs, start=1):
            print(f"[{index:02d}/{len(jobs):02d}] {job.filename}", flush=True)
            try:
                result = run_job(
                    job,
                    root=ROOT,
                    keys=keys,
                    transport=api_transport,
                    max_tokens=args.max_tokens,
                    budget=args.budget,
                    spent=spent,
                )
                spent = result.spent
                completed.append(result.output_path)
                print(f"  成功: {result.output_path.name}，累计 ${spent:.6f}", flush=True)
            except JobFailed as error:
                spent = error.spent
                failures += 1
                print(f"  失败: {error}，累计 ${spent:.6f}", file=sys.stderr, flush=True)
    except BudgetExceeded as error:
        print(f"预算闸门停机: {error}", file=sys.stderr)
        return 3

    validate_ds_files(completed)
    run_full_check(ROOT)
    summary = summarize_usage(read_usage_rows())
    print("\n=== usage 汇总 ===")
    print(
        f"prompt={summary.prompt_tokens} completion={summary.completion_tokens} "
        f"reasoning={summary.reasoning_tokens} total={summary.total_tokens} "
        f"cost=${summary.cost_usd:.6f} avg_latency={summary.average_latency:.3f}s"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
