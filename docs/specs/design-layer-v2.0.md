# 设计层 Schema v2.0 · 定稿（骑手条款主体）

> 状态：**定稿·待双侧采纳**（2026-07-13）。基线：`DM-skill-v1.9.md`（设计层正典）。
> 本文件是引擎侧编译器与产品侧（golden set / check.py / 裁判）**共用的同一份 v2.0**。
> 变更主轴（骑手条款）：v1.9 的 `rules[]{flavor_name, mechanic, plain_rule, visibility}` 里，载荷参数驮在 `plain_rule` 散文里——**可裁判、不可编译**。v2.0 给每条 rule 增一个**结构化 `params` 块**（按 mechanic 判别），`plain_rule` 降为**纯人读散文**（编译器不读它，裁判/玩家读它）。机制/道具/可见性白名单**一律不变**（沿用 v1.9 固定库）。
>
> **词汇纪律**：本文件所有 mechanic / visibility / effect / prop 取值，一字不差取自 `DM-skill-v1.9.md`。params 的**字段键**为 v2.0 新增，就此冻结——两侧以本文件为唯一权威源。
>
> **正典回写（C5–C7 + 现实物品正典句，字段键冻结）**：本轮把待确认项 C5（数字道具引用 `prop:`/`prop_reveal:`，§1 约定 + §4 桥表注）、C6（现实物品 `props_required` 新增顶层字段，§0 表 + §4）、C7（纯原语可达账本封闭·命名空间文法版，§2 第三条不变量）与现实物品正典句（"给指称不给感知 + ask 共识唯一通道 + 属性即证词"，落 `spec-prop-library-v0-final.md` §1.4）写入定稿。`props_required` 与 `prop:<名>` / `prop_reveal:<名>` 引用文法**字段键自此冻结**，须在 goldenset 迁移开跑前生效（v1.8 迁移件 `props_required` 默认 `[]`）。

---

## 0. 与 v1.9 的关系（改什么 / 不改什么）

| | v1.9 | v2.0 |
|---|---|---|
| 顶层输出字段 | game_title/subtitle/premise/players/props_dealt/flow/rules/settlement/reveal_text/reveal_signature/flavor_glossary/safety_note | **+`props_required`**（新增顶层字段，C6）——现实物品备物清单，自由字符串数组；余字段不变（见 §4 定义与四道箍） |
| rules[] 结构 | {flavor_name, mechanic, plain_rule, visibility}（+可选规则属性『常驻』驮在散文里） | **+params +persistent**：{flavor_name, mechanic, plain_rule, visibility, **params**, **persistent?**}（见 §1.1） |
| plain_rule 角色 | 既给人读、又是唯一参数来源 | **仅人读散文**（不含机器参数） |
| mechanic 枚举 | 15 串（DM-skill 行 163） | **不变** |
| visibility 原子 | 自己看/额头/全场公开（行 164） | **不变** |
| settlement | {scoring:[{event,who,effect}], win, tiebreak} | **不变**（v2.0 用 params.scoring_ref 显式回指 scoring.event，落实 stage1 路由表三项静态可查） |

**引擎侧对应版本**：执行层 `script.schema.json` v0.3 → **v0.4**（`settlement`→`finale` 改名，避让本设计层 settlement 正典；见 `机制正典.md` §六）。设计层 settlement 名称**不动**。

**C6 新顶层字段 `props_required`**：现实物品（线下真实道具，如瓶子/筷子/花生/平板）的备物清单，自由字符串数组、**不建受控词表**（词表 v2.1 由真实字符串反向构建，先收后建）。空列表 = 纯数字局（旧「纯数字须为空」条废弃）。与 `props_dealt`（数字道具，走 14 库白名单）**命名空间隔离**：现实物品**给指称不给感知**，永不进任何机器槽（见 §2 C7 不变量、§4 定义与四道箍、`spec-prop-library-v0-final.md` §1.4）。**字段键就此冻结。**

---

## 1. rules[].params —— 13 机制参数块（按 mechanic 判别 / oneOf discriminator）

**约定**：
- `scoring_ref`（字符串或字符串数组）指向 `settlement.scoring[].event` 的 event 名——落实路由表 v1「每条 rule 的结果必须引用 scoring 中某条 event」。
- 值为 mechanic/visibility/effect 枚举处，取 v1.9 canonical 串；`$gen.*` 表内容填装点，`$派生:` 表引擎按局面派生。
- **数字道具引用（C5，字段键冻结）**：机器槽位（`verify_source`/`draw_from`/`reveal_of`/`on_*` 等）以 `prop:<名>` / `prop_reveal:<名>` 形态引用数字道具——`<名>` 必取自 14 道具固定库（§4）且属本局 `props_dealt` 实发清单（引用未发道具 = 空引用，硬闸）。数字道具经 `PROP_REGISTRY` 静态归约到六原语，故机器槽引用它**不违反** §2 C7 账本纯原语可达；`prop_reveal:` 归约到 `show` 原语的可见性翻转。道具**自身不设 params 块**：固定行为归 `PROP_REGISTRY`，可变旋钮由调用机制的 params 承载。现实物品（`props_required`）命名空间**永不**出现在机器槽（§2 C7）。
- **MVP** 列标注引擎编译器 v0 是否覆盖；非 MVP 机制**设计层照常定义**（产品侧 golden set / check.py 覆盖全 13），但引擎编译器返回 `{station:"compile", check:"mechanic_supported", text:"机制「X」暂不可编译（引擎 v0 覆盖 MVP 7 + 回合推进）"}` 干净错误。
- **`回合推进` 例外**：它非 MVP 7 之一，但 `=简单轮转`、**现引擎唯一真实现**（`turn_order:round_robin`，见 `机制正典.md` §一 #11 标『顺带』）——编译器**放行、不返回暂不可编译**。故引擎 v0 可编译 = MVP 7 + 回合推进 = 8 机制；真正返回『暂不可编译』的是余下 **5 个**（点名目标/转移/揭示/受限沟通/传递链）。

### MVP 7（引擎 v0 编译器覆盖）

```jsonc
// mechanic: "同时提交"  —— 全员私密提交、一起揭晓再结算（杜绝偷看，v1.9 行92）
{
  "prompt": "给每位玩家看的提交提示",
  "input_kind": "options | free_text",
  "options": ["…"],                    // input_kind=options 时；可填 "$派生:" 由引擎按局面生成
  "timeout_s": 30,
  "reveal": "simultaneous",            // 固定同揭
  "scoring_ref": ["结果事件名…"]         // → settlement.scoring[].event
}

// mechanic: "限时"  —— 给某动作加时间压力（v1.9 行89）
{
  "seconds": 60,
  "visible_countdown": true,           // 沙漏道具的客户端皮
  "on_timeout": { "effect": "goto | scoring", "goto": "阶段名", "scoring_ref": "…" }
}

// mechanic: "判定"  —— 三判源（v1.9 行95）；MVP 只编 consensus
{
  "source": "consensus | expr | ai",   // expr(规则表达式) / ai(语义仲裁,可被全场一键推翻) → 暂不可编译
  "question": "判定什么",
  "verdict_options": ["过", "不过"],     // source=consensus：全场共识确认的选项
  "expr": "<判定表达式>",                // source=expr 专用（MVP 未启用；判别器现在留）。v2.1 硬闸：须为可解析表达式且引用 state 键（如 "state:虚假声明数 > 0"），散文自由文本拒
  "ai_overridable": true,              // source=ai 固定 true（人类共识永远最高）
  "on": { "过": { "scoring_ref": "…" }, "不过": { "scoring_ref": "…" } }
}

// mechanic: "惩罚(轻)" / "惩罚(中)" / "惩罚(重)"  —— 档位由机制名定死（v1.9 行87：必须选定一档，删 tier_by）
{
  "who": "actor | target | loser | all",
  "pool": "$gen.penalty_内容池",        // 惩罚"内容"是风味；"严重度"是机制名承载，params 不含档位字段
  "scoring_ref": "…"                   // 对应 scoring.effect = "惩罚(轻|中|重)"
}

// mechanic: "加减分"  —— 分数 ±（v1.9 行86）
{
  "who": "actor | target | all_except_actor | winner | loser",
  "delta": "+N | -N",                  // 与 scoring.effect 同形
  "scoring_ref": "…"
}

// mechanic: "声明质疑"  —— 公开声明(可撒谎)+任何人质疑触发揭示验证(v1.9 行91：大话骰/骗子牌/过关申报都是它)
//   焊两条不变量：v1.7 身份终局化（涉身份验证时）+ A_v18 到顶强制摊牌（作逐轮加注结构时，如大话骰）
{
  "claim_prompt": "声明什么",
  "challengers": "others | alive_others",
  "challenge_window_s": 10,
  "verify_source": "prop_reveal:<道具> | judge",
  // —— v1.7（仅当 verify_reveals=identity 且局面为 N≤4 且核心身份唯一，见 §2）——
  "verify_reveals": "prop_state | identity",
  "identity_resolution": "terminal | reshuffle",  // verify_reveals=identity 时必填：
                                                   //   terminal=验证即触发终局结算；reshuffle=验证后身份立即重洗重发
  // —— A_v18（仅当本机制建模为逐轮加注/递增，如大话骰的『叫价逐轮升高』，v1.9 行91/109）——
  "raising": false,                                // true=逐轮加注结构；false=一次性申报（过关申报/单次claim）
  "raise_cap": 6,                                  // raising=true 必填：叫价上限（A_v18 递增序列上限）
  "on_cap": "force_challenge",                     // raising=true 必填：到顶强制摊牌=『下家强制质疑或自动开盅』(v1.9 行109)
  "on_liar": { "scoring_ref": "…" },              // 说谎方受罚
  "on_false_accuse": { "scoring_ref": "…" }       // 冤枉方受罚
}

// mechanic: "续押喊停"  —— 继续博更大 or 收手保住，爆掉失去累积（v1.9 行93）；焊 A_v18 递增不变量
{
  "draw_from": "$gen.事件池 | prop:<道具>",
  "continue_prompt": "继续 / 收手",
  "bust_when": "<爆掉条件表达式>",
  "cap": 8,                            // A_v18 必填：递增序列上限
  "on_cap": "force_settle",           // A_v18 必填：到顶强制摊牌/结算（不存在"无合法行动"状态）
  "bank_on_stop": "<收手保住的累积>",
  "scoring_ref": ["…"]
}
```

### 顺带·现引擎已实现（编译器放行，非"暂不可编译"）

```jsonc
// mechanic: "回合推进"  —— 该谁了（用现实座位顺序，别在虚拟里重排）
// 机制正典 §一 #11『顺带』：=简单轮转，现引擎唯一真实现（turn_order:round_robin）。编译器直接放行。
{ "order": "round_robin" }
```

### 非 MVP 5（设计层定义 / 引擎"暂不可编译"·干净错误）

```jsonc
// mechanic: "点名目标"  —— 指定谁成下一个目标/出局（淘汰出局归此：on_named 含 state alive=false）
{ "selector": "actor | vote", "target_pool": "all | alive_others | …", "on_named": { "scoring_ref": "…", "eliminate": false } }

// mechanic: "转移"  —— 把坏结果甩给别人
{ "what": "score | prop | token", "from": "actor", "to": "chosen | next | prev", "scoring_ref": "…" }

// mechanic: "揭示"  —— 看一条隐藏信息（= 临时翻转某道具可见性对某人为"自己看"）
//   ⚠ v1.7 红线首要针对揭示/偷看类（v1.9 行178）：reveal_of 指向身份牌/核心身份 state 且局面 N≤4 且核心身份唯一时，
//   禁止『翻开身份且游戏继续』——须 identity_resolution(terminal|reshuffle) 或降级为只揭示次级信息（任务/密令），否则 validator/check.py 拒绝。
{ "reveal_of": "prop:<道具> | state:<键>", "to": "actor | chosen | all", "once": true,
  "identity_resolution": "terminal | reshuffle" }   // reveal_of 命中身份/核心身份 state 且 N≤4 唯一身份时必填（见 §2 v1.7）

// mechanic: "受限沟通"  —— 沟通只准走窄通道（v1.9 行94）；系统只保证通道本身合法
{ "channel": "one_word | yes_no_unrelated | one_synonym", "enforce": "channel_only" }

// mechanic: "传递链"  —— 内容按座位序私密逐人传，每人只看上一棒，终局回放对比首尾（v1.9 行96）
{ "content_from": "…", "order": "seat", "each_sees": "prev_only", "replay": "first_vs_last" }
```

### 1.1 规则属性 `persistent`（常驻）—— 与 params 并列的跨机制修饰符

`DM-skill-v1.9.md` 行 98–99 定义规则属性**常驻**：整场后台生效、不占回合、靠玩家一键举报触发判定/惩罚（如"全场禁说某词"）。它是**跨机制的执行修饰符**（可附在 mechanic=`惩罚`/`判定` 等之上），**不是 mechanic**，塞不进按 mechanic 判别的 params oneOf。故在 rules[] 顶层增一个与 params 并列的可选字段：

```jsonc
{ "flavor_name": "…", "mechanic": "惩罚(轻)", "plain_rule": "…", "visibility": "全场公开",
  "params": { … },
  "persistent": { "trigger": "report_driven | background" }   // 可选；缺省=普通占回合规则
}
```

- `persistent.trigger`：`report_driven`（玩家一键举报触发）/ `background`（后台条件常驻生效）。
- **check.py**：`persistent` 存在时校验 trigger 合法，且该规则的结果仍须 `scoring_ref` 对账（§3）。
- **引擎 v0 编译器**：后台常驻 + 举报驱动是**新执行能力**，v0 未实现——含 `persistent` 的 rule **返回 `{station:"compile", check:"mechanic_supported", text:"规则属性『常驻』暂不可编译"}` 干净错误**，绝不静默按普通占回合规则错编（这正是本槽存在的意义：让编译器"看得见"常驻语义、干净报错而非丢失）。

---

## 2. 三条不变量定律（编译器 + validator / check.py 强制，不靠 prompt 自觉）

### A_v18 · 递增不变量（到顶强制摊牌·死锁案定律）
凡 params 定义**递增序列**（累积计数 / 连续加注 / 连翻）者，**必含上限 + 到顶强制结算**。缺 → compile 站 + 语义查双重**硬闸拒绝**。对应 `DM-skill-v1.9.md` 行 109「递增/加注结构必须到顶强制摊牌」（强制摊牌形态明写为『下家强制质疑或自动开盅』）+ 路由表 v1.9 范围第 3 项。**当前适用两处**：
- `续押喊停`（累积计数）：`cap` + `on_cap:force_settle`。
- `声明质疑`**当 `raising:true`（逐轮加注结构，如大话骰——v1.9 行 91 明列大话骰=声明质疑）**：`raise_cap` + `on_cap:force_challenge`（到顶=下家强制质疑）。`raising:false`（一次性申报）不触发本律。

### v1.7 · 身份验证终局化
**门限前提（照 `DM-skill-v1.9.md` 行 178 原文）**：仅当**局面为 N≤4 且核心身份唯一**时，禁止任何『翻开身份且游戏继续』的设计。此前提下，凡揭开核心身份的验证/揭示，须 `identity_resolution` 取 `terminal`（验证即触发终局结算）或 `reshuffle`（验证后身份立即重洗重发失效），或降级为**只揭示次级信息**（任务/密令，不碰核心身份）。N≥5 或身份非核心唯一时，按信息账本一般推理可允许验证后继续。
- **原文红线首要针对『揭示/偷看类』规则**（行 178），**不限于**声明质疑。**当前适用两处**：
  - `声明质疑`（`verify_reveals:identity` 且命中门限前提时）；
  - `揭示`（`reveal_of` 指向身份牌 / 核心身份 state 且命中门限前提时）。
- **静态校验的保守代理**：`核心身份唯一` 这一局面结构在纯静态层难判定，validator/check.py 以 `verify_reveals:identity` / `reveal_of` 命中身份为**保守代理触发** `identity_resolution` 必填——**这是明知过严的代理**（可能对 N≥5 或身份非核心的局也要求终局化）。宁过严勿放过身份翻牌漏洞（v1.7 是实测两破的病根）；真正的『N≤4 且核心身份唯一』判定留待 agent 桌信息流仿真（`spec-judge-v0.2.md` 行 66）精确化。

### C7 · 纯原语可达（账本封闭 · 命名空间文法版）
凡 event 的产生路径**必须可静态归约到六原语**（`show / ask / random / timer / state / fx`）——账本封闭，无悬空语义、无散文旁路。以**机器槽位取值文法**强制之：一切被引擎读取用于判定/取数的槽（`verify_source` / `draw_from` / `reveal_of` / `source` / `selector` / `on_*` 分支等）取值文法为——

> **机器槽 ::= {六原语结构}　∪　{ `prop:<名>` | `prop_reveal:<名>`，其中 `<名> ∈ props_dealt ∩ 14 道具固定库 }**

- **数字道具引用合法**：`prop:` / `prop_reveal:` 经 `PROP_REGISTRY` 静态归约到六原语（见 §1 约定 C5），故其出现在机器槽**不违反**本律；账本仍纯原语可达。
- **`props_required` 命名空间（现实物品）出现在任何机器槽 = 硬闸 error**：现实物品**给指称不给感知**，判定永远走六原语，绝不把物理事件本身当判源。现实物品的落点只在 plain_rule / 任务 / 惩罚等**人读散文**（§4、`spec-prop-library-v0-final.md` §1.4）；物理结果进结算的唯一合法通道是 `ask` 全场共识（tap / 提交 / 计时 / 证词对账）。
- **实现为「机制→不变量」映射表驱动**（同 A_v18）：未来新增机器槽随注册自动纳入文法闸；任何 scoring event 都须能沿槽文法回溯到六原语组合。

---

## 3. settlement 三项静态可查（路由表 v1 · rule↔scoring 对账）

来自 `stage1-final-routing-v1.md` 行 53–56，v2.0 用 `params.scoring_ref` 落实：

1. **rule→scoring 对账**：每条 rule 的 `params.scoring_ref` 引用的 event，必须在 `settlement.scoring[].event` 里存在。
2. **win 引用有源**：`settlement.win` 引用的量，必须在 scoring 里有**数值增减来源**——即至少一条 scoring 条目的 `effect` 为 `+N` 或 `-N`。**`惩罚(档)` 不计作数值量的增减来源**（`effect` 三形态 +N/-N/惩罚(档) 中，惩罚是机制严重度、非分数增减，见 `DM-skill-v1.9.md` 行 87/150）；若 win 引用总分而 scoring 仅含 惩罚(档) 条目 → 判无源、拒绝。
3. **tiebreak 必填**：`settlement.tiebreak` 非空（默认"系统随机裁决"，可穿风味皮）。

---

## 4. 词汇冻结表（两侧唯一权威源）

**mechanic 枚举（15 串，取 `DM-skill-v1.9.md` 行 163 一字不差）**：
点名目标 / 转移 / 加减分 / 惩罚(轻) / 惩罚(中) / 惩罚(重) / 揭示 / 限时 / 回合推进 / 声明质疑 / 同时提交 / 续押喊停 / 受限沟通 / 判定 / 传递链

**visibility 原子（3 值，行 164）**：自己看 / 额头 / 全场公开（含义=信息初始朝向；道具内置翻转不写进 visibility）

**scoring.effect 形态（行 150）**：`+N` / `-N` / `惩罚(轻|中|重)`

**props_dealt 字段**：`{prop, to, visibility, note}`；prop 取自 14 道具固定库（行 68–81）

**设计层道具名 ↔ 引擎执行层 id 桥（MVP 4 件；其余随编译器覆盖逐步补）**：

| 设计层（中文，DM-skill 固定库） | 引擎 id（`PROP_REGISTRY`） | MVP |
|---|---|---|
| 身份牌（额头档） | `head_card` | ✅ |
| 骰盅 | `dice_cup` | ✅ |
| 记分板 | `score` | ✅ |
| 沙漏 | `hourglass` | ✅ |
| 密语卡 / 盲盒 / 虚拟左轮 / 定时炸弹 / 命运转盘 / 扑克牌 / 骰子 / 抽签筒 / 匿名投票器 / 公共看板 | 待编译器覆盖时登记 | ✗ |

**桥表注（C5 · 数字道具引用，字段键冻结）**：params 内 `prop:<名>` / `prop_reveal:<名>` 引用的道具名，须落在上表设计层道具名（14 库）内且属本局 `props_dealt` 实发清单。check.py 以与 `scoring_ref` 收集器同构的 **`prop_ref` 收集器**递归校验：引用名 ∈ 14 库 → 硬闸；引用 ∈ props_dealt 实发 → 硬闸（引用未发 = 空引用）；实发道具无任何 params 引用 → 软闸 `dead_prop:<道具>`（供裁判第 1 项确认，不拒件）——**v2.1 口径修正**：`dead_prop` 只对**可引用**道具计；**免引用**道具（匿名投票器/沙漏/记分板/公共看板，见 `spec-prop-library-v0-final.md` §5「引用类型」列 + `whitelist.json` `prop_reference_types`）靠机制结构消费、不经 `prop:` 引用，实发未引用**不记** `dead_prop`；validator/check.py **读表派生免引用集、不硬编码**。桥表随编译器覆盖逐步补 id，不影响设计层引用合法性——数字道具经 `PROP_REGISTRY` 归约到六原语（§2 C7），机器槽引用它合法。

**`props_required` 字段（C6 · 现实物品 / 新增顶层字段，字段键就此冻结）**：自由字符串数组，线下真实道具的备物清单，**不建受控词表**（词表 v2.1 由真实字符串反向构建，先收后建）；空列表 = 纯数字局（旧「纯数字须为空」条废弃）。与 `props_dealt`（14 库数字道具）**命名空间隔离**。check.py 对字段本体只判类型（字符串数组），另加四道箍：①**机器槽隔离**——props_required 项永不被任何机器槽解析（§2 C7，硬闸）；②每项须在 plain_rule / 任务 / 惩罚内容中被引用，否则软闸 `dead_real_prop:<物>`；③自由串过 contentFilter、safety_note 联动（硬闸——自由词表的代价是安全滤前置）；④编译器透传为开局备物清单（纯展示数据，不产生 Script 节点）。供给侧闭环 `props_required ⊆ 配局库存` 归 prompt 纪律 + 配局 UI 确认，**不归 check.py**。

---

## 5. 连带改动清单（交产品侧新 Fable 对话同步）

> 两侧必须同一份 v2.0。以下为交接单——本文件即唯一权威源。

| 资产 | 改动 | 归属 |
|---|---|---|
| **DM-skill prompt v1.9 → v2.0** | ①【输出格式】rules[] 增 params 块字段（本文件 §1 逐机制定义 + 一行示例）；②【字段取值纪律】增"参数一律进 params，plain_rule 只写给人读、不含机器参数"；③【输出前自检】增两条不变量自检（递增有 cap/on_cap、身份验证终局化）；④固定库/机制白名单**不动**。golden set 全量迁移（每份补 rules[].params）——**启用乙脚手架**（AI 试译→人工审→定稿，产物只进 golden set 不进生产链） | 产品侧 |
| **check.py** | +params 块结构校验（per-mechanic 必填键 oneOf 判别）；+§3 三项静态可查（rule→scoring 对账 / win 引用有源=至少一条 ±N / tiebreak 必填）；+§2 两条不变量（递增 cap/on_cap 必填含声明质疑 raising、身份 identity_resolution 必填含揭示命中身份）；+`persistent` 字段校验（§1.1）；plain_rule 从"机器读取源"断言除名；散文↔参数一致性抽查记软闸 | 产品侧 |
| **裁判 v0.2 第 7 条（结算完整）** | 现文（`spec-judge-v0.2.md` 行 33–34）已查"各结果落到加减分或惩罚 / 平局随机裁决 / 规则间不矛盾"。v2.0 下"各结果落到 scoring"由 check.py 的 rule→scoring 对账**确定性**承接（裁判第 7 项从"跨字段拼装"降为"对账"，见路由表 v1 行 53）；裁判第 7 项保留可玩性层面的一致性判断，不与 check.py 重复 | 产品侧 |
| **引擎 script.schema.json v0.3 → v0.4** | `settlement`→`finale` 改名（避让本设计层 settlement）；编译器新建 `server/compiler/`：设计层 v2.0 →（7 机制模板×4 道具）→ 执行层 finale-schema Script；validator 焊 §2 两条不变量 | 引擎侧（本仓） |

---

## 6. plain_rule 的新定位（骑手条款的另一半）

v2.0 后 `plain_rule` **不再驮任何机器参数**——秒数进 `params.seconds`、选项进 `params.options`、计分进 `params.scoring_ref`/`delta`、惩罚档进 mechanic 名。plain_rule 只剩一件事：**给完全不懂梗的人一句照做无误的白话**（约束 B，`spec-prop-library-v0-final.md` §2）。check.py 断言 plain_rule 存在且非空，但**不再从中解析任何参数**；参数正确性全部由 params 块 + §2/§3 保证。

---

## 7. v2.1 增量（validator / check.py 两侧同规格移植 · 字段键冻结）

> 两条增改：机械可判、口径统一。产品侧 `check.py` 与引擎侧 AiParty `validator` 以本节为唯一权威源同步移植。

### ① expr 可解析硬闸（判定 source=expr）

`判定` 机制 `source=expr` 时，`params.expr` **须为可解析表达式且引用 state 键**，否则硬闸拒。

- **文法**：`expr` 至少含一个 `state:<键>` 引用；把 `state:<键>` / `prop:<名>` / `prop_reveal:<名>` 折成占位标识符后，余下须能解析为**单个表达式**，且只含**比较 / 布尔 / 算术 / 一元 / 常量 / 标识符**结点。`$gen.` / `$派生:` 填装点放行（交内容层填实后再校验）。
- **拒的形态**：散文自由文本（无 state 引用、或解析失败、或含函数调用/语句等非法结点）。**活证 `dsT_A_v20_01`**：其 `判定` 规则 `expr="该轮中有未被质疑的虚假声明"` 为散文，无 `state:` 引用 → 硬闸拒。
- **理由**：`source=expr` 是"引擎按规则表达式自动判"的判源（§2 C7 账本须纯原语可达）；散文表达式不可编译、不可静态归约，混入即在 expr 判源开一条散文旁路，与 consensus/ai 判源边界糊掉。

### ② dead_prop 口径修正 + 软闸旁车文件

**口径**：14 道具正典表新增「引用类型」列（`spec-prop-library-v0-final.md` §5；机读镜像 `whitelist.json` `prop_reference_types`）。`dead_prop` 软闸只对**可引用**道具计；**免引用**道具（匿名投票器 / 沙漏 / 记分板 / 公共看板）靠机制结构消费、不经 `prop:` 引用，实发未引用**不记** `dead_prop`。validator/check.py **读表派生免引用集、不硬编码**（新增/改判引用类型只改表、双侧同步）。

**软闸输出载体**：软闸 warning **改写入旁车文件 `<件名>.warnings.json`**（同目录，机读 `{file, spec_version, warnings[]}`），**不回写件内**——被检件保持纯设计层。裁判读『JSON + 旁车 warning』两件。扫描时 `*.warnings.json` 自身排除、不当设计层件回检；无软闸则清掉旧旁车件。

---

*本 v2.0 为定稿，先交用户；v2.1 两条增改见 §7。产品侧据同一份升级 golden set 与 check.py，引擎侧据同一份建编译器与 schema v0.4、validator 同步 §7 两条。字段键就此冻结，两侧改动前如需调整，回本文件改、双侧同步。*
