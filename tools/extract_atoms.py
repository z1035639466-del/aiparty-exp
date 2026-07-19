"""M-int-1 · 原子结构化抽取(本地跑,便宜模型,断点续跑)。

把 inputs/raw/raw-玩法-xhs-20260718.jsonl 的 variants 原文批量抽成
draw_atom 可用的原子,落 inputs/atoms/atoms-v1.jsonl——落成即被引擎自动合并
(modeb/tools.py 启动时读该文件),小红书采集从此进入弹药库。

铁则(吃过 mechanism 模板事故的药):宁空毋编——原文没有的字段留空;
text 一律用 variants 原文,模型只补分类与参数,不改写内容。
工艺修正(审计三条):confidence 规则强制判低;props 词表两档只收 explicit;
name 动宾短名。

用法(本地,竞标同款五家任选):
  export DEEPSEEK_API_KEY=sk-...
  python tools/extract_atoms.py --provider deepseek --limit 500      # 先跑500条看质量
  python tools/extract_atoms.py --provider deepseek                  # 断点续跑全量
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modeb.transports import make_transport  # noqa: E402

PROPS_LEXICON = ["冰块", "纸巾", "手机", "酒", "杯子", "瓶子", "打火机", "骰子", "扑克",
                 "笔", "纸", "气球", "吸管", "硬币", "筷子", "毛巾", "口红", "眼罩", "绳子", "外套"]
ATOM_TYPES = ["条件点名", "任务内容", "完整玩法", "问答题目", "道具挑战", "规则修饰"]
CURRENCIES = ["喝", "表演", "怪造型", "服务"]

SYSTEM = f"""你做派对玩法原子的结构化分类。对每条原文输出一个 JSON 对象,字段:
- "i": 原样回传的序号
- "name": 动宾式中文短名 ≤6 字(如「冰块贴身」「门口迎宾」)
- "atom_type": {"|".join(ATOM_TYPES)} 之一
- "wildness": 1-9(纯温和1-3,饮酒/轻擦边4-6,身体接触/明显擦边7-9)
- "safety_flags": 从 ["饮酒","身体接触","异性互动","逼量嫌疑"] 选,无则 []
- "currency": {"|".join(CURRENCIES)} 之一(原文有喝/罚酒→喝;表演动作→表演;造型→怪造型;跑腿伺候→服务)
- "settlement": 原文明写的输赢/罚则落点,**没有就空串**——宁空毋编,禁止补编
铁则:只分类不改写;拿不准的字段按保守取值。对整批输入输出一个 JSON 数组,数组外不写任何字。"""


def rule_confidence(rec: dict, out: dict) -> str:
    text = rec["text"]
    if out.get("atom_type") not in ATOM_TYPES or not 1 <= int(out.get("wildness", 0)) <= 9:
        return "low"
    if len(text) < 5 or re.search(r"[�]|^\d+[^,。]{0,2}$", text):
        return "low"  # OCR 乱码特征/过短
    return "high"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="inputs/raw/raw-玩法-xhs-20260718.jsonl")
    ap.add_argument("--out", default="inputs/atoms/atoms-v1.jsonl")
    ap.add_argument("--provider", default="deepseek")
    ap.add_argument("--model", default=None)
    ap.add_argument("--batch", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    args = ap.parse_args()

    raws = []
    for line in Path(args.input).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        raws.append({"idx": r["raw_record_index"], "text": "".join(r.get("variants") or [""])})
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            done.add(json.loads(line)["source_ref"]["raw_record_index"])
    todo = [r for r in raws if r["idx"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"待抽 {len(todo)} 条(已完成 {len(done)}),provider={args.provider}")

    transport = make_transport(args.provider, args.model)
    stats = {"ok": 0, "low": 0, "fail": 0}
    with out_path.open("a", encoding="utf-8") as f:
        for i in range(0, len(todo), args.batch):
            chunk = todo[i : i + args.batch]
            payload = json.dumps([{"i": j, "text": r["text"]} for j, r in enumerate(chunk)],
                                 ensure_ascii=False)
            try:
                raw = transport.complete(SYSTEM, [{"role": "user", "content": payload}])
                m = re.search(r"\[.*\]", raw, re.DOTALL)
                items = {it["i"]: it for it in json.loads(m.group(0))} if m else {}
            except Exception as e:
                print(f"批 {i//args.batch} 失败: {e}", file=sys.stderr)
                items = {}
            for j, rec in enumerate(chunk):
                out = items.get(j)
                if not out:
                    stats["fail"] += 1
                    continue
                conf = rule_confidence(rec, out)
                props = [w for w in PROPS_LEXICON if w in rec["text"]]  # 两档之 explicit,代码判非模型判
                atom = {
                    "atom_id": f"xhs-{rec['idx']:05d}",
                    "name": str(out.get("name", ""))[:8] or f"原子{rec['idx']}",
                    "atom_type": out.get("atom_type", "任务内容"),
                    "text_raw": rec["text"],
                    "wildness": int(out.get("wildness", 3)),
                    "props_explicit": props,
                    "safety_flags": [x for x in out.get("safety_flags", []) if isinstance(x, str)],
                    "currency": out.get("currency", "表演"),
                    "settlement": out.get("settlement", ""),
                    "confidence": conf,
                    "source_ref": {"file": Path(args.input).name, "raw_record_index": rec["idx"]},
                }
                f.write(json.dumps(atom, ensure_ascii=False) + "\n")
                stats["ok" if conf == "high" else "low"] += 1
            f.flush()
            print(f"进度 {min(i+args.batch, len(todo))}/{len(todo)} {stats}")
    print("完成:", stats, "→", out_path)


if __name__ == "__main__":
    main()
