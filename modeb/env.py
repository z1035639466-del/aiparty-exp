"""零依赖 .env 加载:仓库根放一个 .env 配一次,永久生效——不必每开终端重 export。

铁律:.env 已 gitignore,key 永不进仓库。已在真实环境里的变量优先(export 覆盖
文件),让临时切 key 仍然方便。纯标准库,与本项目"零依赖"一致。
"""
from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_env(path: str | Path | None = None) -> None:
    """把仓库根 .env 的 KEY=VALUE 注入 os.environ(仅注入尚未设置的键)。幂等。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    p = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:  # 已在环境里的优先,export 可临时覆盖
            os.environ[key] = val
