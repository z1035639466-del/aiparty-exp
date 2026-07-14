# 评审收口 · 引擎与规则评审意见折叠归并

> 收口时间：2026-07-14 ｜ 基准：`origin/master` HEAD = `3ae592e1a281b36a59e2c8f4c3f19b70211d33ad`
> （`audit(steam-tags): Among Us 改判 中性→减值（任务=空间掩护非内容）`，Claude，Tue Jul 14 08:11:24 2026 +0000）
> 口径：只报仓库可核实的真实状态；不重复既有讨论；不擅自合并他人评审分支或改动 master 正典账本。

---

## §A 真实状态快照（可复核）

| 项 | 真实结果 | 复核命令 |
|---|---|---|
| pytest 全套 | **54 passed / 0 failed**（0.1s） | `python3 -m pytest -q` |
| check.py（outputs/*.json） | **exit=1（红）**：201 件 = 73 略 + 128 检 → **27 过 / 101 挂** | `python3 check.py; echo $?` |
| 101 挂拆分 | 旧件预期失败 40（v16×5 / v17×1 / v18×34，缺 v2.0 `settlement`/`params`）＋ v20×41（多为 `ds_B_div` 中间重试迭代与各模型 `_r1` 审阅重试件，非正典绿集） | 见 §C |
| v2.0 主批产出 | **pass@2 = 1/24**（sonnet 1/8、haiku 0/8、dsT 0/8）；B×20 `{complete:13, exhausted_invalid:26}`，5 件「complete」终件 review 全 **破** | `v20_retry_report.md` |

结论摘要：**唯一全绿物是 54 项单元测试；生成/校验管线按设计处于 v0.3 抽审前的门控红态。**

---

## §B 评审意见折叠归并（采纳 / 拒绝 / 延期 / 待裁 + 落点）

### B-1 引擎侧（check.py / run_ds.py / build_v20_report.py）

| 评审意见 | 处置 | 落点（文件 · 提交） |
|---|---|---|
| check.py v2.0 分层硬闸/软闸 + 件头 warning | **已采纳** | `check.py`、`tests/test_check.py` · `35913ac` |
| v2.0 重试出表器（pass@1/pass@2、破因、核心循环密度） | **已采纳** | `build_v20_report.py`、`tests/test_build_v20_report.py` · `0c1473d`；出表 · `cee7892` |
| 首发拦截闸口成因交叉表（31 件 rejection 归因） | **已采纳（归档）** | `reject-causes.md` · `4777dc5` |
| 结构查重（13 首发过闸件，未发现近重复；最近对 0.475） | **已采纳（归档）** | `BACKLOG.md` · `3ae592e` |
| validator 静态 lint 清单（死道具/标签膨胀/字段引用/人数算术/签名素材交叉引用） | **待裁·未落 master** | 仅见 `codex/verdict-v0.2-rescan:BACKLOG.md` · `b93736e`（未合并） |

### B-2 规则侧（04 SOP / DM-skill v2.0 / 评级尺 v2.1 / B 通道法则）

| 评审意见 | 处置 | 落点 |
|---|---|---|
| 04 结算结构与惩罚层（评级尺 v2.1 + 五列打标规程 · 定稿冻结） | **已采纳（冻结）** | `04_结算结构与惩罚层.md` · `ad98a06` |
| 全量五列打标 341 条 | **已采纳（交付）** | `tags-full.md` · `c87b6ec` |
| B 通道打标三法则（Among Us 案：①被砍构件下游依赖 ②增值通道空转不互抵 ③尺样异源天平） | **已采纳（入 B 通道草案）** | `steam-pilot-tags.md` §1.1 + `BACKLOG.md` · `3ae592e` |
| 设计定律：递增/加注到顶强制摊牌；声明质疑收益风险可比 | **延期**（「矩阵跑完前不动 prompt」） | `BACKLOG.md` |
| 自检候选：flow/rules 内部矛盾检查（闷麦20秒、双终局条件） | **延期** | `BACKLOG.md` |
| 适配表：3 人局注意 →（v0.2 改口）N≤5 硬门槛禁作核心循环 | **延期；改口未落 master** | 旧措辞在 `BACKLOG.md`；新措辞仅在 `codex/verdict-v0.2-rescan`（未合并） |
| Steam B 五发现（弹药册字段/链式评语展开/R 补足率预警/Gartic Phone R 口径/第 42 家族提名） | **延期**（全量前处理；家族提名待人工照准） | `BACKLOG.md` · `3ae592e` |

### B-3 产出判决（verdicts on outputs）

| 评审意见 | 处置 | 落点 |
|---|---|---|
| v0.1 判决（矩阵、sonnet+haiku） | **已采纳（基线）** | `outputs/ds_matrix_verdicts_v0.1.md`、`outputs/sonnet_haiku_verdicts_v0.1.md` |
| v0.3 逐件 review（v20 批） | **已采纳（现行）** | `outputs/*.review3.md` |
| v0.2 冻结集重扫（尺更严，≥4 件 过→破 改判） | **待裁·未合并** | `codex/verdict-v0.2-rescan:outputs/frozen_set_verdicts_v0.2.md` · `b93736e` |
| T4 评审交叉表（tags-full 341 账本口径 R×净收益×S） | **待裁·未合并** | `claude/t4-review-crosstabs-wcu6c5:t4-crosstab.md` · `604f081` |

---

## §C 「390 项全绿」核验结论

- 在 `origin/master`（`3ae592e`）全仓检索：**不存在任何计数为 390 的对象**。唯一 `390` 命中是 `usage_log.csv` 一条无关的 `invalid_json` 记录；最接近的 `392` 是 `BACKLOG.md` 里的相似度分值 `0.392`，非计数。
- 现存最大正典计数为 `tags-full.md` 全量 **341 条**；加 steam 试点 8 条 + pilot 16 条 = 365，也不是 390。
- **既不是 390，也没有全绿。** 全绿仅限 54 项单元测试；`check.py` 对产出目录返回 exit=1，v2.0 主批 pass@2 仅 1/24。
- 按要求不凑数：以上为真实数字，如需「全绿」须先过 v0.3 人工抽审并重跑重试批，当前不满足。

---

## §D 阻塞 / 需人工审核的分歧

1. **v2.0 主批未过闸（放行阻塞）**：pass@2 = 1/24，B×20 26/40 exhausted_invalid、5 终件全破。rollout 设计明令「放行前禁止重试与 pass@2」，须先过**人工卡点 #3（v0.3 抽审：2 破 2 过，含指定对账判破与 DS 过件）**。→ 需人工执行 v0.3 抽审。
2. **v0.1 ↔ v0.2 判决冲突未收口**：`verdict-v0.2-rescan` 用更严的尺把 `sonnet_A_v18_01`、`sonnet_C_v18_02`、`haiku_A_v18_02` 等 ≥4 件由「过」改判「破」，冻结集 pass 计数因此悬置。→ 需人工裁定采用 v0.1 还是 v0.2 尺，再决定合并或丢弃该分支。
3. **两条评审分支未合并**：`t4-review-crosstabs`（341 账本交叉表）、`verdict-v0.2-rescan`（v0.2 重扫）。均为只读评审产物；合并即改动 master 正典账本，属人工授权范围，本收口未擅自合并。
4. **04 §6/§7 R 补足口径未裁**（承前次 SOP 抽审遗留）：R 借「通行规则」补足终局是否合规悬而未决；Beer Pong R2 即此类，steam B 试点又预警「8 条中 7 条靠通行规则补足」，全量前放大此缝隙。→ 需人工裁 R 按「本体」还是「方案态」归型（Gartic Phone 案同源）。
5. **适配表改口未落**：「N≤5 硬门槛，禁作核心循环」新措辞只在未合并分支，master BACKLOG 仍是旧措辞。→ 需人工确认是否采纳并落 master。

---

*本文件为收口归并产物，不改动任何被审 JSON、正典账本或他人评审分支；所有判决与合并决策保留给指定人工卡点。*
