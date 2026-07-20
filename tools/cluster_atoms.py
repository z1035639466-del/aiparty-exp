"""M-int-2 · 模糊近重复聚类(本地零 API)。

上一轮清洗(7447c0e)只抓拼接级完全重复(归一化后全等,78 组)。
这里补模糊级:同型原子做字符 3-gram Jaccard,并查集成簇——
"叠罗汉夺杯"五条转述文字互不相等,但 3-gram 重叠远超阈值。

产物只做两件事,不动任何数据(宁空毋编/冻结批不动):
  1. inputs/patterns/clusters-v0.jsonl —— 簇清单(候选模式卡原料);
  2. 终端统计,写卷宗用。
不自动降级、不自动去重:模糊级误伤代价高,去留由蒸馏时人裁。

用法:python tools/cluster_atoms.py [--threshold 0.55]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modeb.tools import load_atom_pool  # noqa: E402

OUT = ROOT / "inputs/patterns/clusters-v0.jsonl"

# 归一化只服务于相似度计算,不写回任何字段
_STRIP = re.compile(r"[\s,。;;:、!?!?~·..\"「」『』()()0-9]+")


def norm(t: str) -> str:
    return _STRIP.sub("", t)


def grams(t: str, n: int = 3) -> frozenset:
    t = norm(t)
    if len(t) < n:
        return frozenset([t]) if t else frozenset()
    return frozenset(t[i:i + n] for i in range(len(t) - n + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / (len(a) + len(b) - inter)


class DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster(pool: list[dict], threshold: float) -> list[list[dict]]:
    """同型内两两比对(2311 条按型分桶,最大桶 ~900,本地秒级)。
    倒排索引预筛:完全无共享 3-gram 的对子连 Jaccard 都不算。"""
    by_type: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(pool):
        by_type[a.get("type", "?")].append(i)
    g = [grams(a.get("text", "")) for a in pool]
    dsu = DSU(len(pool))
    for idxs in by_type.values():
        inv: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            for tri in g[i]:
                inv[tri].append(i)
        cand: dict[int, set[int]] = defaultdict(set)
        for tri, members in inv.items():
            if len(members) > 60:   # 高频 gram(如"的喝酒")当停用词,免得全桶互连
                continue
            for x in members:
                for y in members:
                    if x < y:
                        cand[x].add(y)
        for x, ys in cand.items():
            for y in ys:
                if jaccard(g[x], g[y]) >= threshold:
                    dsu.union(x, y)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pool)):
        groups[dsu.find(i)].append(i)
    return [[pool[i] for i in members]
            for members in groups.values() if len(members) >= 2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.55)
    args = ap.parse_args()
    pool = load_atom_pool()
    clusters = cluster(pool, args.threshold)
    clusters.sort(key=len, reverse=True)
    total = sum(len(c) for c in clusters)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for k, c in enumerate(clusters, 1):
            rep = min(c, key=lambda a: len(a.get("text", "")) or 999)
            f.write(json.dumps({
                "cluster_id": f"clu-{k:03d}", "size": len(c),
                "atom_type": c[0].get("type"),
                "rep_name": rep.get("name"), "rep_text": rep.get("text", "")[:80],
                "wildness_range": [min(a["wildness"] for a in c), max(a["wildness"] for a in c)],
                "variants": [a["id"] for a in c],
            }, ensure_ascii=False) + "\n")
    print(f"阈值 {args.threshold}: {len(clusters)} 簇 / {total} 条原子"
          f"(池 {len(pool)},占 {total / len(pool):.0%})")
    for c in clusters[:12]:
        rep = min(c, key=lambda a: len(a.get("text", "")) or 999)
        print(f"  ×{len(c)}  {rep.get('name')}: {rep.get('text', '')[:46]}")


if __name__ == "__main__":
    main()
