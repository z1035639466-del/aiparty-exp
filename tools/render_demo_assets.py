"""演示资产渲染管线:模式卡 demo_prompt → 图像生成 API → demo/ 落盘 → 回填 demo_ref。

房主裁定(2026-07-21):T2 用图像生成(gptimage2 这类)做序列图,不用视频。
本脚本是"你接 key 我先铺管"的管,支持两条口子(按环境变量自动选路):
  ① OpenAI 兼容 images 口(设了 IMAGE_API_BASE 就走这条,同步取图)——
      IMAGE_API_BASE(如 https://api.openai.com/v1 或任意兼容中转)
      IMAGE_API_KEY
      IMAGE_API_MODEL(如 gpt-image-2)
  ② DashScope(阿里百炼)图像口(没配 IMAGE_API_BASE、但有 DASHSCOPE_API_KEY 时走这条)——
      房主只有一把 DASHSCOPE_API_KEY,调 wan2.7-image-pro / qwen-image-2.0 这类模型出图。
      百炼图片接口是异步任务式:提交任务拿 task_id → 轮询任务状态 → SUCCEEDED 后取图 url 下载。
      DASHSCOPE_API_KEY
      IMAGE_API_MODEL(如 wan2.7-image-pro,默认值即此)
铁律沿用资产线既有规矩:
- 只画动作与队形,槽位留白(prompt 由蒸馏卡自带,生成前追加统一负面约束);
- 资产挂模式:落盘名 = demo/<tier>/<pattern_id>.png,回填 demo_ref 即生效,
  渲染失败不动卡(降级文字照跑,局不断)。

用法:python tools/render_demo_assets.py [--only pat-v1-03] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ROOT / "inputs/patterns/patterns-v0.jsonl"

STYLE_SUFFIX = (
    "。风格:干净的教学示意插画,中性配色,无文字无标语,无品牌,无酒类/杯中物,"
    "人物为无特征简笔角色;多格时按从左到右分格排布,格间留白。"
)

# 万相 2.5–2.7 全系(含 wan2.7-image-pro)统一走 image2image 口,文生图也是它;
# 老 text2image 口对新模型回 "url error"。新版百炼业务空间账号可能连通用域名都不认
# (聊天口就必须走空间专属域名),此时 IMAGE_API_URL 整条覆盖:
#   https://<空间ID>.cn-<region>.maas.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis
DASHSCOPE_SUBMIT_URL = os.environ.get(
    "IMAGE_API_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis")
# 轮询跟随提交域名——空间专属域名下任务查询也在同一域,写死官方域会查不到任务
_SUBMIT_HOST = DASHSCOPE_SUBMIT_URL.split("/api/")[0]
DASHSCOPE_TASK_URL = _SUBMIT_HOST + "/api/v1/tasks/{task_id}"
DASHSCOPE_POLL_INTERVAL = 3  # 秒
DASHSCOPE_POLL_MAX_ROUNDS = 40


def _render_openai(prompt: str, base: str, key: str) -> bytes:
    """走现有 OpenAI 兼容 images 口(同步取图)。"""
    model = os.environ.get("IMAGE_API_MODEL", "gpt-image-2")
    req = urllib.request.Request(
        base.rstrip("/") + "/images/generations", method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        data=json.dumps({"model": model, "prompt": prompt,
                         "size": "1536x1024", "n": 1}).encode())
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    b64 = resp["data"][0].get("b64_json")
    if not b64:  # 有的口回 url
        url = resp["data"][0]["url"]
        with urllib.request.urlopen(url, timeout=180) as r2:
            return r2.read()
    return base64.b64decode(b64)


def _dashscope_dig(resp: dict, *keys: str):
    """从 DashScope 响应里挖字段:先看 output.<key>,兜底看顶层 <key>——接口字段偶有出入,按能跑通来。"""
    output = resp.get("output") if isinstance(resp.get("output"), dict) else {}
    for k in keys:
        if k in output:
            return output[k]
    for k in keys:
        if k in resp:
            return resp[k]
    return None


def _dashscope_result_url(resp: dict) -> str | None:
    results = _dashscope_dig(resp, "results")
    if isinstance(results, list) and results:
        r0 = results[0]
        if isinstance(r0, dict):
            return r0.get("url")
    return None


def _image_params() -> dict:
    params = {"n": 1}
    size = os.environ.get("IMAGE_API_SIZE", "1536*1024")
    if size:
        params["size"] = size
    return params


def _dashscope_submit(prompt: str, key: str, model: str, async_on: bool) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if async_on:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(
        DASHSCOPE_SUBMIT_URL, method="POST", headers=headers,
        data=json.dumps({
            "model": model,
            "input": {"prompt": prompt},
            # IMAGE_API_SIZE 置空串 = 不传 size,交给模型默认(有的档位挑尺寸)
            "parameters": _image_params(),
        }).encode(),
    )
    # 4xx 的真实原因(模型名不存在/尺寸不合法/空间未授权)在响应正文里,
    # 光抛 "HTTP Error 400" 等于没报错——读出来带进异常。
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"DashScope 提交被拒 HTTP {e.code}:{detail}(model={model!r})") from None


def _render_dashscope(prompt: str) -> bytes:
    """走 DashScope(阿里百炼)图像口。老账号是异步任务式(提交→轮询→下载);
    新版业务空间的 key 不支持异步("does not support asynchronous calls"),
    被拒时自动降级为同步一把梭,结果直接在响应里。IMAGE_API_ASYNC=0/1 可强制。"""
    key = os.environ["DASHSCOPE_API_KEY"]
    model = os.environ.get("IMAGE_API_MODEL", "wan2.7-image-pro")
    mode = os.environ.get("IMAGE_API_ASYNC", "")  # "" = 自动:先异步,被拒转同步
    if mode == "0":
        resp = _dashscope_submit(prompt, key, model, async_on=False)
    else:
        try:
            resp = _dashscope_submit(prompt, key, model, async_on=True)
        except RuntimeError as e:
            if mode == "1" or "asynchronous" not in str(e):
                raise
            resp = _dashscope_submit(prompt, key, model, async_on=False)
    # 同步口:图 url 直接在响应里,不用轮询
    url = _dashscope_result_url(resp)
    if url:
        with urllib.request.urlopen(url, timeout=180) as r:
            return r.read()
    task_id = _dashscope_dig(resp, "task_id")
    if not task_id:
        raise RuntimeError(f"DashScope 未返回 task_id:{resp}")

    task_url = DASHSCOPE_TASK_URL.format(task_id=task_id)
    url = None
    for _ in range(DASHSCOPE_POLL_MAX_ROUNDS):
        poll_req = urllib.request.Request(
            task_url, method="GET",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(poll_req, timeout=60) as r:
            resp = json.loads(r.read())
        status = str(_dashscope_dig(resp, "task_status", "status") or "").upper()
        if status == "SUCCEEDED":
            url = _dashscope_result_url(resp)
            break
        if status == "FAILED":
            msg = _dashscope_dig(resp, "message") or resp
            raise RuntimeError(f"DashScope 任务失败:{msg}")
        time.sleep(DASHSCOPE_POLL_INTERVAL)
    if not url:
        raise RuntimeError("DashScope 任务轮询超时或未返回图片 url")

    with urllib.request.urlopen(url, timeout=180) as r:
        return r.read()


def render_one(card: dict, out_dir: Path, dry: bool) -> str | None:
    prompt = (card.get("demo_prompt") or "").strip()
    if not prompt:
        return None
    tier = (card.get("demo_tier") or "T1").lower()
    out = out_dir / f"demo/{tier}/{card['pattern_id']}.png"
    if dry:
        print(f"[dry] {card['pattern_id']} → {out}")
        return None
    full_prompt = prompt + STYLE_SUFFIX
    base = os.environ.get("IMAGE_API_BASE")
    if base:
        key = os.environ.get("IMAGE_API_KEY")
        if not key:
            raise SystemExit("缺 IMAGE_API_KEY 环境变量(配了 IMAGE_API_BASE 需要配对的 key)")
        img = _render_openai(full_prompt, base, key)
    elif os.environ.get("DASHSCOPE_API_KEY"):
        img = _render_dashscope(full_prompt)
    else:
        raise SystemExit(
            "缺 IMAGE_API_BASE/IMAGE_API_KEY,也缺 DASHSCOPE_API_KEY"
            "(房主接其中一路 key 即通,管线已就绪)"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img)
    return str(out.relative_to(out_dir))


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from modeb.env import load_env
    load_env()  # 仓库根 .env 配一次永久生效(key 不进仓库)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只渲染指定 pattern_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cards = [json.loads(l) for l in PATTERNS.read_text(encoding="utf-8").splitlines() if l.strip()]
    changed = False
    for c in cards:
        if args.only and c["pattern_id"] != args.only:
            continue
        if c.get("demo_ref") or not c.get("demo_prompt"):
            continue  # 已有资产的不重渲;无 prompt 的是 T0
        try:
            ref = render_one(c, ROOT, args.dry_run)
        except SystemExit:
            raise
        except Exception as e:  # 渲染失败不动卡:降级文字照跑
            print(f"! {c['pattern_id']} 渲染失败({type(e).__name__}: {e}),跳过")
            continue
        if ref:
            c["demo_ref"] = ref
            changed = True
            print(f"✓ {c['pattern_id']} → {ref}")
    if changed:
        with PATTERNS.open("w", encoding="utf-8") as f:
            for c in cards:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print("demo_ref 已回填;记得 python tools/build_atoms_db.py 重建库")


if __name__ == "__main__":
    main()
