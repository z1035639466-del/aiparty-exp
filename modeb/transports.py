"""传输层实装:Anthropic Messages API + OpenAI 兼容口(DeepSeek 等)。纯标准库。

密钥一律取环境变量,不落盘不入参数默认值。座位用便宜档(haiku/deepseek),
主持可用 sonnet(房主裁定:跑 Sonnet 额度或 Haiku、DS 这种便宜的)。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ANTHROPIC_BASE = "https://api.anthropic.com"

MODELS = {
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}

# 国产五家注册表:与 run_cn_bidding.py(aiparty-cn-bidding-rerun-20260715)同源同配置
CN_PROVIDERS = {
    "minimax": {"base": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.7", "key_env": "MINIMAX_API_KEY"},
    "kimi": {"base": "https://api.moonshot.cn/v1", "model": "kimi-k2.6", "key_env": "MOONSHOT_API_KEY"},
    "glm": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.1", "key_env": "GLM_API_KEY"},
    "qwen": {"base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-plus", "key_env": "DASHSCOPE_API_KEY"},
    "deepseek": {"base": "https://api.deepseek.com", "model": "deepseek-v4-pro", "key_env": "DEEPSEEK_API_KEY"},
}


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url, method="POST",
        headers={"Content-Type": "application/json", **headers},
        data=json.dumps(payload).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 供应商把真正的原因(模型名不存在/参数不合法/余额不足)写在正文里,
        # 光靠状态码没法定位。正文里不含密钥,可以安全带进异常。
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(
            f"{url} 返回 HTTP {e.code}:{detail}(model={payload.get('model')!r})") from None


class AnthropicTransport:
    def __init__(self, model: str = MODELS["haiku"], max_tokens: int = 800,
                 base_url: str | None = None) -> None:
        self.model = MODELS.get(model, model)
        self.max_tokens = max_tokens
        self.base = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or ANTHROPIC_BASE).rstrip("/")

    def complete(self, system: str, messages: list[dict]) -> str:
        # 同一个密钥值,两种放法:官方认 x-api-key;OAuth 与部分中转口只认 Bearer。
        # 二选一发出去——两个头同时发官方会直接拒。
        key = os.environ.get("ANTHROPIC_API_KEY")
        token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        if key:
            auth = {"x-api-key": key}
        elif token:
            auth = {"Authorization": f"Bearer {token}"}
        else:
            raise RuntimeError("缺 ANTHROPIC_API_KEY(或 ANTHROPIC_AUTH_TOKEN)环境变量")
        resp = _post_json(
            f"{self.base}/v1/messages",
            {**auth, "anthropic-version": "2023-06-01"},
            {"model": self.model, "system": system, "messages": messages,
             "max_tokens": self.max_tokens})
        return "".join(b.get("text", "") for b in resp.get("content", []))


class OpenAICompatTransport:
    """OpenAI 兼容口(国产五家全走此口,/chat/completions):system 并入 messages 首条。"""

    def __init__(self, model: str, base_url: str, key_env: str,
                 max_tokens: int = 800) -> None:
        # 注意:不套 MODELS——那是 Anthropic 的别名表,套上来会把 sonnet 翻成
        # claude-sonnet-5 发给国产口,换来一个没头没脑的 400。
        self.model = model
        self.max_tokens = max_tokens
        self.key_env = key_env
        self.base = base_url.rstrip("/")

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
    """provider: anthropic | minimax|kimi|glm|qwen|deepseek(国产五家,竞标同配) | mock。"""
    if provider == "anthropic":
        return AnthropicTransport(model or "haiku")
    if provider in CN_PROVIDERS:
        cfg = CN_PROVIDERS[provider]
        # sonnet/haiku 是 Anthropic 档位别名,对国产口无意义(UI 与 CLI 的出厂默认值
        # 恰好是它们)。此时回落到本家默认模型,而不是把别名原样发出去换 400。
        if not model or model in MODELS:
            model = cfg["model"]
        return OpenAICompatTransport(model, cfg["base"], cfg["key_env"])
    if provider == "mock":
        from .driver_llm import MockTransport
        return MockTransport([])
    raise ValueError(f"未知 provider: {provider}(可用: anthropic/{'/'.join(CN_PROVIDERS)}/mock)")
