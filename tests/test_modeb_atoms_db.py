"""M3 换库:atoms.sqlite 是派生物,jsonl 是源;接口先定库后换,签名不变。

三条铁则:①库与 jsonl 逐字段等价;②源变了没重建 → 跌回 jsonl
(悄悄陈旧比没有库更糟);③显式传 atoms_path 一律走 jsonl 老路(测试不受库干扰)。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.tools import ATOMS_DB, ATOMS_FILE, _load_pool_from_db, load_atom_pool  # noqa: E402
from tools.build_atoms_db import build  # noqa: E402

CMP_KEYS = ("name", "type", "text", "wildness", "tier", "min_players",
            "currency", "props", "safety", "opener", "skill")


def test_db_pool_equals_jsonl_pool():
    db_pool = {a["id"]: a for a in load_atom_pool()}          # 默认:走库
    jl_pool = {a["id"]: a for a in load_atom_pool(ATOMS_FILE)}  # 显式:走 jsonl
    assert set(db_pool) == set(jl_pool)
    for aid, a in jl_pool.items():
        b = db_pool[aid]
        for k in CMP_KEYS:
            assert a.get(k) == b.get(k), f"{aid}.{k}: jsonl={a.get(k)!r} db={b.get(k)!r}"


def test_stale_db_falls_back(tmp_path):
    db_path = tmp_path / "atoms.sqlite"
    build(db_path)
    db = sqlite3.connect(str(db_path))
    db.execute("UPDATE meta SET value='deadbeef' WHERE key='atoms_jsonl_sha1'")
    db.commit()
    db.close()
    assert _load_pool_from_db(db_path) is None, \
        "源 jsonl 指纹对不上 = 库陈旧,必须返回 None 跌回 jsonl"


def test_corrupt_db_falls_back(tmp_path):
    bad = tmp_path / "atoms.sqlite"
    bad.write_bytes(b"not a database")
    assert _load_pool_from_db(bad) is None, "库坏了就当没有,jsonl 永远兜底"


def test_db_carries_pattern_id():
    db = sqlite3.connect(ATOMS_DB)
    (pat,) = db.execute("SELECT pattern_id FROM atoms WHERE id='xhs-01758'").fetchone()
    assert pat == "pat-t1-01", "模式挂载点要落成索引列(交叉握手对峙)"
    (n,) = db.execute("SELECT COUNT(*) FROM patterns").fetchone()
    db.close()
    assert n >= 60, f"T1 8 + 蒸馏 v1 53,实际 {n}"


def test_draw_atom_works_on_db_backed_pool():
    from modeb.state import GameState
    from modeb.tools import ToolExecutor
    ex = ToolExecutor(GameState(players=["甲", "乙", "丙", "丁"],
                                wildness_cap=8, time_budget_min=30))
    r = ex.execute({"name": "draw_atom", "input": {"atom_type": "完整玩法", "tier": "铺垫"}})
    assert r["ok"], f"库背书的池上死亡组合也必须抽得出: {r}"
