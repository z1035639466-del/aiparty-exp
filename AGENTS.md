# Git workflow

After completing and committing any user-requested repository change, always run `git push` for the current branch and report the remote synchronization result in the final response.

# 子 agent 模型调度规范(2026-07-18 房主定)

- **Fable 额度只归房主本人调用**——任何子 agent 一律不得指定 Fable。
- 子 agent 模型由主会话按任务分配:**Opus**(设计/裁定/创意/审计类重活)、
  **Sonnet**(默认,一切常规与批量任务)。**Haiku 退役**(2026-07-19 房主裁定,
  依据 M-int-1 首批实测:text_raw 保真 37% 不符、类型边界误判、88K token 仍抄错
  ——判断类字段不合格;仅当任务产物可被代码 100% 校验兜底时方可特批使用)。
- 派发时在工单里注明所用模型,产物入库提交沿用既有 Co-Authored-By 惯例。

# Python 地板(2026-07-20 定)

- 仓库统一支持 **Python 3.9+**(房主侧机器只有系统自带 3.9.6,无 brew/pyenv)。
- 凡在运行时求值位置(函数签名、dataclass 字段)使用 PEP 604(`int | None`)的文件,
  **必须带 `from __future__ import annotations`**——新文件一律默认带上。
- 不使用 3.10+ 独占的运行时特性(match/case、ParamSpec 运行时用法等)。
