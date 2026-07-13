# 派单碰撞记录

> 记录调度层把同一个矩阵格子重复派给多个 session 的事件。碰撞样本一律隔离，不计入矩阵统计。

## 2026-07-13 · matrix/sonnet_B_v18_02

- **格子**：sonnet · input_B · DM-skill v1.8 · 第2次生成 (`matrix/sonnet_B_v18_02`)
- **现象**：同一个格子被派发给两个独立 session。
  - `session_01TGfbKNNeH4V8Lj7Z8JUs6e` 于 2026-07-12 13:08 先行生成并推送 `matrix/sonnet_B_v18_02`（远程分支、`outputs/sonnet_B_v18_02.json`）。
  - `session_012eskcPGjDkU4X4GBhszsMn`（本次）独立收到同一格子的任务指令，在本地完整现制一局后，推送前才发现远程分支已存在且内容完全不同（两次生成互不知情，玩法设计也不同）。
- **处理**：未覆盖已推送的既有产出。本次结果改推到 `matrix/sonnet_B_v18_02b`（`outputs/sonnet_B_v18_02b.json`），标记为**隔离/碰撞样本**，不进入矩阵格子统计，不合并进 master、不开 PR。
- **根因（待查）**：调度层对同一格子存在重复派单，需要在派单/占用层加去重或占用锁；否则同一 bug 会再次发生，产生更多"重复格子"。
