"""工具面双向对账:实装 ⇄ 申报 必须一字不差。

病历(真机实测,同一个病两个方向各犯过一次):
- random.dice:铁律正文让用、tools.py 实装了,TOOLS_DECLARATION 没申报——局长翻遍
  工具清单找不到,退而抓 random.int 一颗颗挤牙膏,大话骰被逼成快递局;
- skill.cancel:差点反向再犯——申报进了清单,实装却不存在,调了就是"未知子操作"。
本测从 tools.py 源码静态提取全部可调操作,与 TOOLS_DECLARATION 的 name 集合互相
校验。以后加工具是三件套:实装 + 申报 + 本测自动对账,漏任何一半直接红。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOOLS_SRC = (Path(__file__).resolve().parent.parent / "modeb" / "tools.py").read_text()

# 家族函数 → 申报名前缀。裸名工具(show/ask/…)函数存在即算实装。
_FAMILIES = {
    "_t_show": "show", "_t_ask": "ask", "_t_fx": "fx", "_t_timer": "timer",
    "_t_draw_atom": "draw_atom",
    "_t_random": "random", "_t_state": "state", "_t_skill": "skill",
    "_t_duel": "duel", "_t_judge": "judge", "_t_music": "music",
}
_BARE = {"show", "ask", "fx", "timer", "draw_atom"}


def _implemented_ops() -> set:
    """按 _t_* 函数块切源码,块内 name=="x.y" 与 op=="z" 都算该家族的实装操作。"""
    ops = set()
    spans = [(m.start(), m.group(1)) for m in re.finditer(r"def (_t_\w+)\(", TOOLS_SRC)]
    spans.append((len(TOOLS_SRC), ""))
    for (start, fn), (end, _) in zip(spans, spans[1:]):
        family = _FAMILIES.get(fn)
        if not family:
            continue
        block = TOOLS_SRC[start:end]
        if family in _BARE:
            ops.add(family)
        for full in re.findall(r'name == "([a-z_]+\.[a-z_]+)"', block):
            ops.add(full)
        for op in re.findall(r'op == "([a-z_]+)"', block):
            ops.add(f"{family}.{op}")
        # 默认分支写法:op = … if "." in name else "start" / a.get("op", "play")
        for op in re.findall(r'if "\." in name else "([a-z_]+)"', block):
            ops.add(f"{family}.{op}")
        for op in re.findall(r'a\.get\("op", "([a-z_]+)"\)', block):
            ops.add(f"{family}.{op}")
    return ops


def _declared_ops() -> set:
    from modeb.driver_llm import TOOLS_DECLARATION
    return {t["name"] for t in TOOLS_DECLARATION}


def test_every_implemented_op_is_declared():
    missing = _implemented_ops() - _declared_ops()
    assert not missing, f"实装了但没申报(局长不知道它存在): {sorted(missing)}"


def test_every_declared_op_is_implemented():
    ghosts = _declared_ops() - _implemented_ops()
    assert not ghosts, f"申报了但没实装(局长调了就被钳): {sorted(ghosts)}"
