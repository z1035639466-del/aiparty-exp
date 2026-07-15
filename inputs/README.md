# inputs 登记册（存量欠账清点 · golden A–D + gold/fail 双档案）

> 用途：把阶段 1 遗留的输入集、输出档案与 DS 运行口径一次清点落盘，供后续 v2.0 验证批可追溯。
> 只登记既有存量，不改任何历史件；口径以源码/排除清单为准，不凭记忆。

## 1. Golden 输入集 A–D（`inputs/input_{A,B,C,D}.json`）

配局输入四件套，每件含 `venue / players / 口味(6 滑块) / 素材`。口味滑块固定六维：烈度 · 黄暴 · 脑力体力 · 节奏 · 亲密 · 混乱（0–100）。

| 件 | venue | N | 烈度 | 黄暴 | 脑力体力 | 节奏 | 亲密 | 混乱 | 素材 | 探针定位 |
|---|---|---|---|---|---|---|---|---|---|---|
| **A** | 老王家客厅 | 3 | 96 | 52 | 70 | 80 | 45 | 78 | 3 | 高烈度小局（重金属 / 食堂三楼梗 / 狼人杀）——张力天花板样本 |
| **B** | 公司团建包间 | 5 | 55 | 20 | 30 | 85 | 25 | 60 | 5 | 职场分寸 + 大 N（N=5），**B×20 多样性批**的基底输入 |
| **C** | 毕业旅行的民宿客厅 | 4 | 70 | 65 | 50 | 40 | 88 | 35 | 4 | 高亲密（88），密友场合 |
| **D** | 周五晚的桌游吧包间 | 4 | 30 | 15 | 10 | 50 | 60 | 25 | 4 | 低烈度 / 低脑力体力——温和局，`input_D` 探针模式（道具入库回归用） |

侧挂探针 `input_A_prime_probe.json`：取 v2.1.1 正典的 A 样例输入，仅删除现实库存中的“空酒瓶”；只供 `sonnet_A2_probe_{01,02}`，不入主表、不入分母。

## 2. 输出档案 `outputs/`（gold / fail 双档案）

清点口径：`outputs/*.json` 共 **74** 份、`*.review.md` 裁判/自玩旁注 **12** 份。校验以 `check.py` + `check_exclusions.json` 为准（**排除清单 14 项不入任何统计**）。

### 2a. Gold 档案（计入冻结矩阵：过 check.py + 裁判 v0.2）

- **基准件**：`fable_A_v18_r1.json`——目前唯一**双审双过**（裁判 v0.2 + 纸面自玩）的局，golden 资产之首（见 `docs/specs/stage1-final-routing-v1.md`）。
- **v2.0 主批候选**（按路由表命名，每模型每输入 2 发）：
  - `sonnet_{A–D}_v18_{01,02}`（8 发）
  - `haiku_{A–D}_v18_{01,02}`（8 发逻辑；`haiku_D_v18_02` 失败→`_r1` 终件）
  - `ds_{A–D}_v18_{01,02}`（8 发逻辑，DS 非思考矩阵；`ds_B_v18_02` 失败→`_r1` 终件）
  - `dsT_{A–D}_v18_{01,02}`（8 发，DS 思考矩阵 thinking/high）
- **B×20 多样性批**：`ds_B_div_01..20`（20 发逻辑；6 发无效 JSON 已由 `_r1/_r2` 重试终件替代）。
- **Fable 演进谱系**（历史留痕，非当前矩阵主力）：`fable_A_v{11,16,16_r1,17,18,18_r1}`、`fable_B_v{11,16_01,16_02}`、`fable_C_v11`、`fable_D_v16`。
- **裁判档案**：`ds_matrix_verdicts_v0.1.md`、`sonnet_haiku_verdicts_v0.1.md`。

### 2b. Fail 档案（`check_exclusions.json`，14 项，保留但不入统计）

| 类 | 数 | 文件 | 缘由 |
|---|---|---|---|
| 历史 v1.1 | 3 | `fable_{A,B,C}_v11_01.json` | v1.1 输出，不计入当前冻结矩阵 |
| 失败原件 / 重试原件 | 11 | `haiku_D_v18_02`、`ds_B_v18_02`、`ds_B_div_{01,04,12,15,15_r1,16,16_r1,17,18}` | 无效 JSON / 保留为重试证据；均有 `_r1`/`_r2` 终件替代 |

> Fail 档案的价值：留作重试证据与失败模式样本；`run_ds.py` 的 `_r{n}` 命名规约让「成功重试映射回逻辑件名」可断点续跑。

## 3. DS 运行口径（`run_ds.py` `--thinking` 实义 · 读源码确认，勿凭记忆）

**`--thinking {off,on,both}` 是任务批筛选器**（按每个 `Job.thinking` 布尔位选择跑哪些发），**不是**对同一批作业的全局思考开关：

- **`off`** → 全部 `thinking=False` 作业 = 8 发 `ds_*_v18` 非思考矩阵 + 20 发 `ds_B_div` 多样性批（payload `thinking:{type:disabled}`、无 `reasoning_effort`）。
- **`on`** → 只跑 8 发 `dsT_*_v18` 思考矩阵（payload `thinking:{type:enabled}` + `reasoning_effort:high`）。
- **`both`** → 全 36 发。

依据：`build_jobs()`（三批：8 非思考矩阵 + 8 思考矩阵 + 20 多样性）、`select_jobs()`（按 `thinking` 过滤）、`make_payload()`（`thinking`/`reasoning_effort` 注入）；`tests/test_run_ds.py::test_thinking_cli_filters_batches_but_diversity_remains_off` 断言 `off=28 / on=8 / both=36`——**关键：20 发 B 多样性批恒为非思考，只在 `off`/`both` 出现，`on` 不含它**。
