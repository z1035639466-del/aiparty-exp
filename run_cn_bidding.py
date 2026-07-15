"""Run the five-provider China-model bidding batch without persisting API keys.

The runner performs exactly three independent requests per provider, never
retries a generation, and writes the model's response text verbatim to the
reserved output path.  API keys are read with hidden terminal input and live
only in this process.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
SYSTEM_PATH = ROOT / "docs" / "specs" / "DM-skill-v2.0.md"
INPUT_PATH = ROOT / "inputs" / "input_B.json"
OUTPUTS_DIR = ROOT / "outputs"
USAGE_PATH = ROOT / "cn_bidding_usage.csv"

TEMPERATURE = 0.8
DEFAULT_MAX_TOKENS = 16_000
RUNS_PER_PROVIDER = 3

USAGE_FIELDS = [
    "filename",
    "provider",
    "requested_model",
    "response_model",
    "thinking_mode",
    "prompt_token",
    "completion_token",
    "reasoning_token",
    "cached_prompt_token",
    "latency_seconds",
    "cost_currency",
    "cost_amount",
    "input_price_per_m",
    "output_price_per_m",
    "cache_hit_price_per_m",
    "status",
]


@dataclass(frozen=True)
class Provider:
    slug: str
    display_name: str
    base_url: str
    requested_model: str
    input_price_per_m: float
    output_price_per_m: float
    cache_hit_price_per_m: float
    currency: str
    thinking_mode: str
    extra_body: dict[str, Any]


PROVIDERS = (
    Provider(
        slug="minimax_m2_7",
        display_name="MiniMax",
        base_url="https://api.minimaxi.com/v1",
        requested_model="MiniMax-M2.7",
        input_price_per_m=2.1,
        output_price_per_m=8.4,
        cache_hit_price_per_m=0.42,
        currency="CNY",
        thinking_mode="required",
        extra_body={"reasoning_split": True, "service_tier": "standard"},
    ),
    Provider(
        slug="kimi_k2_6",
        display_name="Kimi",
        base_url="https://api.moonshot.cn/v1",
        requested_model="kimi-k2.6",
        input_price_per_m=6.5,
        output_price_per_m=27.0,
        cache_hit_price_per_m=1.1,
        currency="CNY",
        thinking_mode="enabled",
        extra_body={"thinking": {"type": "enabled"}},
    ),
    Provider(
        slug="glm_5_1",
        display_name="智谱",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        requested_model="glm-5.1",
        input_price_per_m=6.0,
        output_price_per_m=24.0,
        cache_hit_price_per_m=1.3,
        currency="CNY",
        thinking_mode="enabled",
        extra_body={"thinking": {"type": "enabled"}},
    ),
    Provider(
        slug="qwen3_7_plus",
        display_name="阿里云百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        requested_model="qwen3.7-plus",
        input_price_per_m=1.6,
        output_price_per_m=6.4,
        cache_hit_price_per_m=0.32,
        currency="CNY",
        thinking_mode="enabled",
        extra_body={"enable_thinking": True},
    ),
    Provider(
        slug="deepseek_v4_pro",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        requested_model="deepseek-v4-pro",
        input_price_per_m=0.435,
        output_price_per_m=0.87,
        cache_hit_price_per_m=0.003625,
        currency="USD",
        thinking_mode="enabled/high",
        extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    ),
)


class TransportError(RuntimeError):
    def __init__(self, message: str, raw_body: str = "") -> None:
        super().__init__(message)
        self.raw_body = raw_body


def build_payload(provider: Provider, system_text: str, user_text: str, max_tokens: int) -> dict[str, Any]:
    temperature = 1.0 if provider.slug == "kimi_k2_6" else TEMPERATURE
    payload: dict[str, Any] = {
        "model": provider.requested_model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    payload.update(provider.extra_body)
    return payload


def api_transport(api_key: str, provider: Provider, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    request = urllib.request.Request(
        f"{provider.base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise TransportError(f"HTTP {error.code}", raw) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransportError(f"network_error: {type(error).__name__}") from error
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TransportError("response_not_json", raw) from error
    if not isinstance(parsed, dict):
        raise TransportError("response_not_object", raw)
    return parsed, raw


def extract_content(response: dict[str, Any]) -> str:
    content = response["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("choices[0].message.content is not a string")
    return content


def _integer_or_unavailable(value: Any) -> int | str:
    if isinstance(value, bool):
        return "unavailable"
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return "unavailable"


def extract_usage(response: dict[str, Any]) -> dict[str, int | str]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {
            "prompt": "unavailable",
            "completion": "unavailable",
            "reasoning": "unavailable",
            "cached": "unavailable",
        }

    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        details = {}
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}

    reasoning_value = details.get("reasoning_tokens")
    if reasoning_value is None:
        reasoning_value = usage.get("reasoning_tokens")

    cached_value = prompt_details.get("cached_tokens")
    if cached_value is None:
        cached_value = usage.get("prompt_cache_hit_tokens")

    return {
        "prompt": _integer_or_unavailable(usage.get("prompt_tokens")),
        "completion": _integer_or_unavailable(usage.get("completion_tokens")),
        "reasoning": _integer_or_unavailable(reasoning_value),
        "cached": _integer_or_unavailable(cached_value),
    }


def calculate_cost(provider: Provider, usage: dict[str, int | str]) -> float | str:
    prompt = usage["prompt"]
    completion = usage["completion"]
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return "unavailable"
    cached = usage["cached"] if isinstance(usage["cached"], int) else 0
    if cached < 0 or cached > prompt:
        return "unavailable"
    uncached = prompt - cached
    amount = (
        uncached * provider.input_price_per_m
        + cached * provider.cache_hit_price_per_m
        + completion * provider.output_price_per_m
    ) / 1_000_000
    return round(amount, 9)


def append_usage(row: dict[str, Any], path: Path | None = None) -> None:
    if path is None:
        path = USAGE_PATH
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=USAGE_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def output_path(provider: Provider, sequence: int) -> Path:
    return OUTPUTS_DIR / f"cn_{provider.slug}_B_bid_v20_{sequence:02d}.json"


def run_one(
    provider: Provider,
    api_key: str,
    sequence: int,
    *,
    system_text: str,
    user_text: str,
    max_tokens: int,
    transport: Callable[[str, Provider, dict[str, Any]], tuple[dict[str, Any], str]] = api_transport,
) -> Path:
    path = output_path(provider, sequence)
    path.parent.mkdir(exist_ok=True)
    handle = path.open("x", encoding="utf-8")
    started = time.perf_counter()
    response: dict[str, Any] | None = None
    raw_response = ""
    content = ""
    status = "failed"
    try:
        payload = build_payload(provider, system_text, user_text, max_tokens)
        response, raw_response = transport(api_key, provider, payload)
        content = extract_content(response)
        status = "received"
    except TransportError as error:
        content = error.raw_body
        status = str(error)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        content = raw_response
        status = f"missing_content: {type(error).__name__}"
    finally:
        handle.write(content)
        handle.close()

    latency = time.perf_counter() - started
    usage = extract_usage(response or {})
    response_model = (response or {}).get("model")
    if not isinstance(response_model, str) or not response_model:
        response_model = "unavailable"
    cost = calculate_cost(provider, usage)
    append_usage(
        {
            "filename": path.name,
            "provider": provider.display_name,
            "requested_model": provider.requested_model,
            "response_model": response_model,
            "thinking_mode": provider.thinking_mode,
            "prompt_token": usage["prompt"],
            "completion_token": usage["completion"],
            "reasoning_token": usage["reasoning"],
            "cached_prompt_token": usage["cached"],
            "latency_seconds": f"{latency:.6f}",
            "cost_currency": provider.currency,
            "cost_amount": cost,
            "input_price_per_m": provider.input_price_per_m,
            "output_price_per_m": provider.output_price_per_m,
            "cache_hit_price_per_m": provider.cache_hit_price_per_m,
            "status": status,
        }
    )
    return path


def prompt_token_upper_bound(system_text: str, user_text: str) -> int:
    return len((system_text + user_text).encode("utf-8")) + 1_024


def print_estimate(
    system_text: str,
    user_text: str,
    max_tokens: int,
    providers: tuple[Provider, ...] = PROVIDERS,
) -> None:
    prompt_ceiling = prompt_token_upper_bound(system_text, user_text)
    print(f"固定 prompt 的保守输入上界: {prompt_ceiling} tokens")
    for provider in providers:
        per_call = (
            prompt_ceiling * provider.input_price_per_m
            + max_tokens * provider.output_price_per_m
        ) / 1_000_000
        print(
            f"{provider.display_name} {provider.requested_model}: "
            f"{RUNS_PER_PROVIDER} 发完整上界 {provider.currency} {per_call * RUNS_PER_PROVIDER:.6f}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--estimate", action="store_true", help="只打印保守成本上界（默认）")
    mode.add_argument("--run", action="store_true", help="执行 15 次付费 API 调用")
    parser.add_argument("--yes", action="store_true", help="确认已取得用户对付费调用的动作时授权")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument(
        "--start-at",
        choices=[provider.slug for provider in PROVIDERS],
        help="Resume at this provider without repeating earlier completed providers.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=[provider.slug for provider in PROVIDERS],
        help="Run only this provider; repeat the option to select multiple providers.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_tokens <= 0:
        print("max_tokens 必须大于 0", file=sys.stderr)
        return 2

    system_text = SYSTEM_PATH.read_text(encoding="utf-8")
    user_text = INPUT_PATH.read_text(encoding="utf-8")
    providers = PROVIDERS
    if args.start_at and args.only:
        print("--start-at 与 --only 不能同时使用。", file=sys.stderr)
        return 2
    if args.only:
        selected = set(args.only)
        providers = tuple(provider for provider in PROVIDERS if provider.slug in selected)
    elif args.start_at:
        start_index = next(
            index for index, provider in enumerate(PROVIDERS) if provider.slug == args.start_at
        )
        providers = PROVIDERS[start_index:]
    print_estimate(system_text, user_text, args.max_tokens, providers)
    if not args.run:
        print("预估模式：未发送任何 API 请求。")
        return 0
    if not args.yes:
        print("拒绝执行：付费调用需要 --yes。", file=sys.stderr)
        return 2

    for provider in providers:
        api_key = getpass.getpass(f"请输入 {provider.display_name} API Key（输入不回显）：")
        if not api_key:
            print(f"{provider.display_name}: 未输入 Key，停止。", file=sys.stderr)
            return 2
        try:
            for sequence in range(1, RUNS_PER_PROVIDER + 1):
                path = run_one(
                    provider,
                    api_key,
                    sequence,
                    system_text=system_text,
                    user_text=user_text,
                    max_tokens=args.max_tokens,
                )
                print(f"{provider.display_name} [{sequence}/{RUNS_PER_PROVIDER}] -> {path.name}", flush=True)
        finally:
            api_key = "\0" * len(api_key)
            del api_key

    print(f"{len(providers) * RUNS_PER_PROVIDER} 次独立调用已结束；Key 未写入文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
