"""尾巴波次合并:子 agent 判断字段 + 代码侧事实回填 → 并入 atoms-v1.jsonl。

纪律(M-int-1 实测教训固化):
- text_raw / source_ref 一律由本脚本从 handoff 源按 raw_record_index 回填,
  模型只出判断字段——保真 100% 由构造保证,不靠模型自觉;
- 枚举不合规范 → confidence 降 low + quarantine 注记,不硬修不丢弃;
- 与现役 high 原子撞归一化全等 → 近重复降级(保留版=老 id);
- 中文夹空格(OCR 断字)→ text_clean 修复,text_raw 永不改动。

用法:python tools/merge_tail_atoms.py <out目录含 tail-out-*.jsonl>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "inputs/raw/raw-玩法-xhs-20260718.jsonl"
ATOMS = ROOT / "inputs/atoms/atoms-v1.jsonl"
TYPES = {"完整玩法", "条件点名", "任务内容", "道具挑战", "问答题目", "规则修饰", "技能授予"}
CURR = {"喝", "表演", "服务", "怪造型"}
SAFE = {"饮酒", "身体接触", "异性互动", "逼量嫌疑", "外发不可逆"}
_PUNCT = re.compile(r"[\s,。;;:、!?!?~·..「」()()\"']+")
_CJK_GAP = re.compile(r"(?<=[一-鿿]) +(?=[一-鿿])")


def norm(t: str) -> str:
    return _PUNCT.sub("", t or "")


def main(out_dir: str) -> None:
    raw = {r["raw_record_index"]: r for r in
           map(json.loads, RAW.read_text(encoding="utf-8").splitlines())}
    existing = [json.loads(l) for l in ATOMS.read_text(encoding="utf-8").splitlines()]
    have_ids = {a["atom_id"] for a in existing}
    norm_high = {norm(a.get("text_clean") or a["text_raw"]): a["atom_id"]
                 for a in existing if a.get("confidence") == "high" and not a.get("quarantine")}

    judgments = []
    for f in sorted(Path(out_dir).glob("tail-out-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    judgments.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"! 丢弃坏行({f.name}): {line[:60]}")

    stats = {"in": len(judgments), "skip": 0, "new": 0, "dup": 0,
             "bad_enum": 0, "seam_fixed": 0, "missing_src": 0, "already": 0}
    out_rows = []
    for j in sorted(judgments, key=lambda x: x.get("i", 0)):
        i = j.get("i")
        src = raw.get(i)
        if src is None:
            stats["missing_src"] += 1
            continue
        aid = f"xhs-{i:05d}"
        if aid in have_ids:
            stats["already"] += 1
            continue
        if j.get("skip"):
            stats["skip"] += 1
            continue
        text_raw = " ".join(src.get("variants") or [])
        atom = {
            "atom_id": aid, "name": str(j.get("name") or aid)[:12],
            "atom_type": j.get("atom_type"), "text_raw": text_raw,
            "wildness": min(9, max(1, int(j.get("wildness") or 3))),
            "props_explicit": [str(p) for p in (j.get("props_explicit") or [])][:6],
            "safety_flags": [s for s in (j.get("safety_flags") or []) if s in SAFE],
            "currency": j.get("currency"),
            "settlement": str(j.get("settlement") or "")[:60],
            "confidence": j.get("confidence") if j.get("confidence") in ("high", "low") else "low",
            "source_ref": {"file": RAW.name, "raw_record_index": i},
            "wave": "tail-20260721",
        }
        if atom["atom_type"] not in TYPES or atom["currency"] not in CURR:
            stats["bad_enum"] += 1
            atom["confidence"] = "low"
            atom["quarantine"] = "字段不合规范(尾巴波,枚举越界)"
        clean = _CJK_GAP.sub("", text_raw)
        if clean != text_raw:
            atom["text_clean"] = clean
            atom["seam_fixed"] = True
            stats["seam_fixed"] += 1
        n = norm(atom.get("text_clean") or text_raw)
        if n and n in norm_high:
            atom["confidence"] = "low"
            atom["quarantine"] = f"近重复坏版(保留版={norm_high[n]})"
            stats["dup"] += 1
        else:
            if n and atom["confidence"] == "high" and "quarantine" not in atom:
                norm_high[n] = aid  # 尾巴内部也互撞
            stats["new"] += 1
        out_rows.append(atom)

    with ATOMS.open("a", encoding="utf-8") as f:
        for a in out_rows:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    high = sum(1 for a in out_rows if a["confidence"] == "high" and "quarantine" not in a)
    print(f"合并完成:{stats} | 新增行 {len(out_rows)},其中 high 入弹药 {high}")
    print("下一步:python tools/build_atoms_db.py && python3 -m pytest tests/ -q")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "/tmp/claude-0/-home-user-aiparty-exp/3fdbbb72-ad31-51ac-9bc2-2130a072f139/scratchpad")
