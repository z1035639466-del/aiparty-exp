"""构建 atoms.sqlite —— M3 换库第一步:接口先定库后换,draw_atom 签名不变。

口径唯一:直接复用 modeb.tools.load_atom_pool / load_pattern_cards 产出的
内存池(tier、min_players 同源计算),再落库——库永远是派生物,jsonl 是源。
meta 表存源文件指纹:jsonl 改了没重建,加载层会发现并跌回 jsonl
(悄悄陈旧比没有库更糟)。

用法:python tools/build_atoms_db.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modeb.tools import ATOMS_FILE, PATTERNS_FILE, load_atom_pool, load_pattern_cards  # noqa: E402

DB_FILE = ROOT / "inputs/atoms/atoms.sqlite"

DDL = """
DROP TABLE IF EXISTS atoms;
DROP TABLE IF EXISTS patterns;
DROP TABLE IF EXISTS meta;
CREATE TABLE atoms (
  id TEXT PRIMARY KEY, name TEXT, type TEXT, text TEXT,
  wildness INT, tier TEXT, min_players INT, currency TEXT,
  props TEXT, safety TEXT, opener INT, skill TEXT, pattern_id TEXT
);
CREATE INDEX idx_atoms_facets ON atoms(type, tier, wildness, min_players);
CREATE TABLE patterns (
  pattern_id TEXT PRIMARY KEY, name TEXT, skeleton TEXT,
  demo_ref TEXT, demo_tier TEXT, slot_note TEXT
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else "absent"


def build(db_path: Path | str = DB_FILE, atoms_path: str | None = None,
          patterns_path: str | None = None) -> dict:
    pool = load_atom_pool(atoms_path)
    cards = load_pattern_cards(patterns_path)
    pat_by_atom = {aid: c["pattern_id"] for c in cards for aid in c["variants"]}
    db = sqlite3.connect(str(db_path))
    db.executescript(DDL)
    db.executemany(
        "INSERT INTO atoms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(a["id"], a.get("name"), a.get("type"), a.get("text"),
          a.get("wildness"), a.get("tier"), a.get("min_players"),
          a.get("currency"),
          json.dumps(a.get("props", []), ensure_ascii=False),
          json.dumps(a.get("safety", []), ensure_ascii=False),
          1 if a.get("opener") else 0,
          json.dumps(a["skill"], ensure_ascii=False) if a.get("skill") else None,
          pat_by_atom.get(a["id"]))
         for a in pool])
    db.executemany(
        "INSERT INTO patterns VALUES (?,?,?,?,?,?)",
        [(c["pattern_id"], c.get("name"), c.get("skeleton"),
          c.get("demo_ref"), c.get("demo_tier"), c.get("slot_note"))
         for c in cards])
    meta = {
        "schema_version": "1",
        "atoms_jsonl_sha1": _sha1(ROOT / (atoms_path or ATOMS_FILE)),
        "patterns_jsonl_sha1": _sha1(ROOT / (patterns_path or PATTERNS_FILE)),
        "n_atoms": str(len(pool)), "n_patterns": str(len(cards)),
    }
    db.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    db.commit()
    db.close()
    return meta


if __name__ == "__main__":
    m = build()
    print(f"atoms.sqlite 重建完成:{m['n_atoms']} 条原子,{m['n_patterns']} 张模式卡")
