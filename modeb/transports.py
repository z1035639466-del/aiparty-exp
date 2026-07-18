"""传输层实装:Anthropic Messages API + OpenAI 兼容口(DeepSeek 等)。纯标准库。

密钥一律取环境变量,不落盘不入参数默认值。座位用便宜档(haiku/deepseek),
主持可用 sonnet(房主裁定:跑 Sonnet 额度或 Haiku、DS 这种便宜的)。
"""
from __future__ import annotations

import json
import os
import urllib.request

ANTHROPIC_BASE = "https://api.anthropic.com"
DEEPSEEK_BASE = "https://api.deepseek.com"

MODELS = {
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "deepseek": "deepseek-chat",
}


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, method="POST",
        headers={"Content-Type": "application/json", **headers},
        data=json.dumps(payload).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class AnthropicTransport:
    def __init__(self, model: str = MODELS["haiku"], max_tokens: int = 800,
                 base_url: str | None = None) -> None:
        self.model = MODELS.get(model, model)
        self.max_tokens = max_tokens
        self.base = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or ANTHROPIC_BASE).rstrip("/")

    def complete(self, system: str, messages: list[dict]) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("缺 ANTHROPIC_API_KEY 环境变量")
        resp = _post_json(
            f"{self.base}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": self.model, "system": system, "messages": messages,
             "max_tokens": self.max_tokens})
        return "".join(b.get("text", "") for b in resp.get("content", []))


class OpenAICompatTransport:
    """DeepSeek 等 OpenAI 兼容口:system 并入 messages 首条。"""

    def __init__(self, model: str = MODELS["deepseek"], max_tokens: int = 800,
                 base_url: str | None = None, key_env: str = "DEEPSEEK_API_KEY") -> None:
        self.model = MODELS.get(model, model)
        self.max_tokens = max_tokens
        self.key_env = key_env
        self.base = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE).rstrip("/")

    def complete(self, system: str, messages: list[dict]) -> str:
        key = os.environ.get(self.key_env)
        if not key:
            raise RuntimeError(f"缺 {self.key_env} 环境变量")
        resp = _post_json(
            f"{self.base}/chat/completions",
            {"Authorization": f"Bearer {key}"},
            {"model": self.model, "max_tokens": self.max_tokens,
             "messages": [{"role": "system", "content": system}] + messages})
        return resp["choices"][0]["message"]["content"]


def make_transport(provider: str, model: str | None = None):
    """provider: anthropic | deepseek | mock(测试)。model 可用别名 sonnet/haiku/deepseek。"""
    if provider == "anthropic":
        return AnthropicTransport(model or "haiku")
    if provider == "deepseek":
        return OpenAICompatTransport(model or "deepseek")
    if provider == "mock":
        from .driver_llm import MockTransport
        return MockTransport([])
    raise ValueError(f"未知 provider: {provider}")
