"""局长开口:DashScope(阿里百炼)千问 TTS 适配器。纯标准库,ready-to-plug。

房主原话:"这些玩意我弄个api就有了,你写好到时候我接进去就行了"——所以本模块
只认环境变量:配了 key 就通,没配就整体静默跳过(configured() 为 False,
simulator 的 /api/tts 回 404 说明,不报错不崩)。密钥一律环境变量,不落盘。

环境变量(与 transports.base_for 同一套"可配优先、硬编码兜底"写法):
  TTS_API_KEY   → 回落 DASHSCOPE_API_KEY(房主一把百炼 key 全模态通)
  TTS_BASE_URL  → 回落 DASHSCOPE_BASE_URL → 官方 dashscope 地址
  TTS_MODEL     → 默认 qwen3-tts
  TTS_VOICE     → 默认 Cherry(千问 TTS 女声;控制台音色名不同时一把覆盖)

接口形态(按 DashScope 原生多模态生成口;字段全做成模块级常量,
房主对着控制台微调只改常量/环境变量,不用动逻辑):
  POST {base}/api/v1/services/aigc/multimodal-generation/generation
  headers: Authorization: Bearer <key>
  body: {"model": <model>, "input": {"text": <text>, "voice": <voice>}}
  resp: output.audio.data(base64 内联)或 output.audio.url(下载链接)——
        两种都见过,先内联后下载,与 render_demo_assets 的"按能跑通来"同款容错。

TTS 是出口不是监听:本模块只把主持已说出口的字变成声音,不含任何 ASR/录音。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

# —— 模块级常量:URL 路径 / 请求体字段名,便于房主对着控制台微调 —— #
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com"
TTS_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_MODEL = "qwen3-tts"
DEFAULT_VOICE = "Cherry"
FIELD_TEXT = "text"        # input 里放文本的字段名
FIELD_VOICE = "voice"      # input 里放音色的字段名
EXTRA_INPUT: dict = {}     # 控制台要求带额外 input 字段(如 language_type)时填这里
TTS_MIME = "audio/mpeg"    # /api/tts 响应的 Content-Type(口子实际回 wav 时改这里)
TIMEOUT_S = 60


class TTSError(RuntimeError):
    """TTS 传输错误,风格随 transports.TransportError(带状态码,code=None=网络层)。
    故意不继承 TransportError:TTS 是锦上添花的出口,失败不该进主持的
    Resilient 重试/静默拍逻辑,谁调 synthesize 谁就地接住、局照跑。"""

    def __init__(self, msg: str, code: int | None = None) -> None:
        super().__init__(msg)
        self.code = code


def api_key() -> str | None:
    """密钥:TTS_API_KEY 优先(专口),否则回落 DASHSCOPE_API_KEY(一把 key 全通)。"""
    return os.environ.get("TTS_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")


def configured() -> bool:
    """有 key 即视为已接入;没配就整体静默跳过(调用方据此不烧网络)。"""
    return bool(api_key())


def base_url() -> str:
    """base:TTS_BASE_URL 优先(业务空间专属地址,同 transports.base_for 的理由),
    否则 DASHSCOPE_BASE_URL,否则官方地址。"""
    return (os.environ.get("TTS_BASE_URL")
            or os.environ.get("DASHSCOPE_BASE_URL")
            or DASHSCOPE_BASE).rstrip("/")


def build_payload(text: str, voice: str | None = None) -> dict:
    """请求体单拎成函数:测试验构造、房主校准控制台都看这一处。"""
    model = os.environ.get("TTS_MODEL") or DEFAULT_MODEL
    voice = voice or os.environ.get("TTS_VOICE") or DEFAULT_VOICE
    return {"model": model,
            "input": {FIELD_TEXT: text, FIELD_VOICE: voice, **EXTRA_INPUT}}


def _fetch(url: str, headers: dict, data: bytes | None = None) -> bytes:
    """一次 HTTP 往返 → 原始字节。错误统一转 TTSError(正文不含密钥,可安全带出)。"""
    req = urllib.request.Request(url, method="POST" if data else "GET",
                                 headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise TTSError(f"{url} 返回 HTTP {e.code}:{detail}", e.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TTSError(f"{url} 网络层失败:{e}") from None


def synthesize(text: str, voice: str | None = None) -> bytes:
    """一句主持词 → 音频字节。未配 key 或口子异常一律抛 TTSError,调用方接住降级。"""
    key = api_key()
    if not key:
        raise TTSError("TTS 未接入:缺 TTS_API_KEY(或 DASHSCOPE_API_KEY)环境变量")
    text = (text or "").strip()
    if not text:
        raise TTSError("空文本不合成(没词可念)")
    payload = build_payload(text, voice)
    raw = _fetch(base_url() + TTS_PATH,
                 {"Content-Type": "application/json",
                  "Authorization": f"Bearer {key}"},
                 json.dumps(payload, ensure_ascii=False).encode())
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError:
        raise TTSError(f"TTS 响应不是 JSON:{raw[:200]!r}") from None
    output = resp.get("output") if isinstance(resp.get("output"), dict) else {}
    audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
    # 先内联 base64,后下载 url——接口字段偶有出入,按能跑通来
    if audio.get("data"):
        try:
            return base64.b64decode(audio["data"])
        except (ValueError, TypeError):
            raise TTSError("TTS 返回的 audio.data 不是合法 base64") from None
    if audio.get("url"):
        return _fetch(audio["url"], {})
    raise TTSError(f"TTS 响应缺 output.audio.data/url:{str(resp)[:300]}")
