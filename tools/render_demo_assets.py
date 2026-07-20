"""演示资产渲染管线:模式卡 demo_prompt → 图像生成 API → demo/ 落盘 → 回填 demo_ref。

房主裁定(2026-07-21):T2 用图像生成(gptimage2 这类)做序列图,不用视频。
本脚本是"你接 key 我先铺管"的管:走 OpenAI 兼容 images 口,三个环境变量即通——
  IMAGE_API_BASE(如 https://api.openai.com/v1 或任意兼容中转)
  IMAGE_API_KEY
  IMAGE_API_MODEL(如 gpt-image-2)
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = ROOT / "inputs/patterns/patterns-v0.jsonl"

STYLE_SUFFIX = (
    "。风格:干净的教学示意插画,中性配色,无文字无标语,无品牌,无酒类/杯中物,"
    "人物为无特征简笔角色;多格时按从左到右分格排布,格间留白。"
)


def render_one(card: dict, out_dir: Path, dry: bool) -> str | None:
    prompt = (card.get("demo_prompt") or "").strip()
    if not prompt:
        return None
    tier = (card.get("demo_tier") or "T1").lower()
    out = out_dir / f"demo/{tier}/{card['pattern_id']}.png"
    if dry:
        print(f"[dry] {card['pattern_id']} → {out}")
        return None
    base = os.environ.get("IMAGE_API_BASE")
    key = os.environ.get("IMAGE_API_KEY")
    model = os.environ.get("IMAGE_API_MODEL", "gpt-image-2")
    if not base or not key:
        raise SystemExit("缺 IMAGE_API_BASE / IMAGE_API_KEY 环境变量(房主接 key 即通,管线已就绪)")
    req = urllib.request.Request(
        base.rstrip("/") + "/images/generations", method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        data=json.dumps({"model": model, "prompt": prompt + STYLE_SUFFIX,
                         "size": "1536x1024", "n": 1}).encode())
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read())
    b64 = resp["data"][0].get("b64_json")
    if not b64:  # 有的口回 url
        url = resp["data"][0]["url"]
        with urllib.request.urlopen(url, timeout=180) as r2:
            img = r2.read()
    else:
        img = base64.b64decode(b64)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img)
    return str(out.relative_to(out_dir))


def main() -> None:
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
