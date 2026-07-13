# 散文 → params 映射表（乙脚手架蒸馏产物 · 给编译站与未来 lint 用）

> 依据迁移单 §4：试译中每个"散文→params"的对应模式记一行。迁移完成即乙退场，
> 生产路径 AI 直接产 v2.0 格式（DM-skill v2.0 正典 prompt），本表转为编译站对照/lint 素材。
> 逐件累积；首件来源 `fable_A_v18_r1 → fable_A_v20_m1`。

| # | 散文模式（v1.8 plain_rule/flow 形态） | v2.0 落点 | 备注 | 首见件 |
|---|---|---|---|---|
| 1 | "声称'…至少有 N 个 X'（可以吹牛）" | 声明质疑 `claim_prompt` | 声明内容整句进 prompt | A_v20_m1 |
| 2 | "质疑上一口…开盅数骰子/开X验货" | 声明质疑 `verify_source: prop_reveal:<道具>` + `verify_reveals: prop_state` | 开验道具须 ∈ props_dealt ∩ 14 库 | A_v20_m1 |
| 3 | "新叫价必须比上一口更大" | 声明质疑 `raising: true` | 触发 A_v18 递增定律 → #4 必填 | A_v20_m1 |
| 4 | "上限是'M 个 X'…到顶自动摊牌/视同被质疑" | 声明质疑 `raise_cap: M` + `on_cap: force_challenge` | 点数位 X 留散文；到顶语义即 A_v18 强制摊牌 | A_v20_m1 |
| 5 | "够数则…判输；不够数则…判输" | `on_liar` / `on_false_accuse` 各带 `scoring_ref` | 一对一结算双向时双指 [输事件, 赢事件] | A_v20_m1 |
| 6 | "系统立即自动判定〈条件〉" | 判定 `source: expr` + `expr: <条件表达式>` | 非共识、非 AI 仲裁的机器自动判；expr 非 MVP、引擎干净报错 | A_v20_m1 |
| 7 | "判输的每人 -1 分，判赢的每人 +1 分" | 加减分 `who/delta/scoring_ref` + settlement.scoring 双 event（±N） | 单条 params 只承载一个方向，另一方向由 event.effect 承载 | A_v20_m1 |
| 8 | "判输的人当场〈表演式惩罚〉…可喊'跳过'温和替代" | 惩罚(档) `who: loser` + `pool: $gen.penalty_内容池` + `scoring_ref` | 档位进机制名；内容全留散文；event.effect 须同档 | A_v20_m1 |
| 9 | "思考/行动时间为 T 秒（沙漏计）" | 限时 `seconds: T` + `visible_countdown: true` | 沙漏是倒计时的客户端皮，不占引用槽 | A_v20_m1 |
| 10 | "超时…视同你喊了'X'/视同某动作" | 限时 `on_timeout: {effect: goto, goto: <结算入口>}` | 单分支槽；多分支超时语义（如"首叫超时系统代叫"）留散文，lint 应放行 | A_v20_m1 |
| 11 | "按现实座位顺时针轮流，不重排座位" | 回合推进 `order: round_robin` | 定值键；"输家先叫"类首叫规则无槽、留散文 | A_v20_m1 |
| 12 | "各自私密提交'…'，全员提交后同时揭晓" | 同时提交 `prompt/input_kind/reveal: simultaneous` | 指认在场玩家 → `input_kind: options` + `options: $派生:在场玩家名单` | A_v20_m1 |
| 13 | "猜中X者各 +A；若无人猜中，X +B；完成任务再 +C" | settlement.scoring 按分支拆 event，rule 侧 `scoring_ref` 数组逐一对账 | 一句散文多分支 → 多条 scoring | A_v20_m1 |
| 14 | "平局或并列，默认由系统随机裁决（风味名'Y'）" | `settlement.tiebreak` 原句 | v1.9 三键之一，必填 | A_v20_m1 |
| 15 | "…总分最高者夺冠" | `settlement.win` 原句 | win 引用分数 → scoring 须有 ±N 来源（C3） | A_v20_m1 |
| 16 | 必填机器键在原件散文**无对应数值** | `"$派生:<说明>"` 占位 | 转写不发明：缺参不造数，标 $派生: 交人工审定 | A_v20_m1 |
| 17 | 参数驮在**别条**规则的散文里（跨条驮参） | 抽进用参那条的 params；预期 `prose_param_mismatch` 软闸 | 如叫价窗 10 秒驮在'闷麦十秒'条，抽进 声明质疑.challenge_window_s | A_v20_m1 |
