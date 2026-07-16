# 04_UGC河流采集地图 — Codex 总执行清单

> 本文件是 Codex 的采集 SOP。上游 01/02/03 已覆盖全球**机制骨架**(106 母机制)。
> 本文件采的是**活体 UGC 层**:局头(带局人)角色词 + 局头原子(玩法/罚则),重点是**新鲜长尾**,不是重扒存量清单。

---

## 0. 本轮核心目标（焦点已收窄）

机制骨架已足够。本轮关键目标是两层:

1. **找到"局头"= 喝酒局的主持人/带局人这一角色**,挖出各文化的本地角色词,顺着角色词找到内容最集中的创作者和语料。
2. 顺带持续收集**局头原子**(单发玩法/罚则/触发卡),喂 `$gen` 内容池。

**局头定义**:懂全套玩法、能把气氛带起来、发起并主持游戏的那个人。找到这个角色词 = 找到金矿入口,因为内容就是这批人生产和分享的。

---

## 1. 认知框架（为什么这么采）

- **机制层 vs 活体河流**:01/02/03 是静态百科式的"哪些游戏存在";本文件是"局头和玩法在哪条河里天天被重新发明"。入口完全不同。
- **小红书 ≠ 西方单一平台,是一套分工**:发现/种草层(Pinterest/IG)+ 演示层(TikTok/YouTube)+ 正典/长尾讨论层(Reddit/韩国论坛)。别问"西方的小红书是谁",要问"角色词和玩法的发现层/演示层/正典层各自在哪"。
- **采 delta,不采清单**:骨架已有,活体层只抓新发明、变体、评论区长尾、屏幕/BGM 类新品类。

---

## 2. 平台河流图（全球）

| 平台 | Claude 侧能力 | Codex 需要的权限 | 覆盖大头 |
|---|---|---|---|
| **小红书** | 几乎为零(robots+登录墙全封) | **你的登录态**,必开 | 华语绝对大头 |
| **抖音** | 少量 | **你的登录态**(真独立区,与国际版不通) | 华语视频层 |
| **TikTok** | 只能搜 discover 页文字,抓不了正文/视频/评论 | 浏览器+**视频转写**+读评论 | 英西意法葡德多语 |
| **Instagram** | 几乎为零 | 你的登录态 | 印度 antakshari/kitty |
| **Facebook 群** | 几乎为零 | 你的登录态 | 菲律宾 parlor games |
| **YouTube** | 标题/简介,看不了视频/字幕 | 浏览器+**字幕提取** | 韩国 술게임 demo |
| **Pinterest** | board 标题+pin 描述(搜索摘要还行) | 浏览器(账号可选) | 印度/派对视觉灵感 |
| **韩国论坛/wiki**(오르비·나무위키) | 摘要意外全,深读被封 bot | 走 **dump**,不登号 | 韩国 술게임全库 |
| **Reddit** | 基本隐形 | 走**免费存档**,不登号 | 英美讨论/正典 |
| **聚合站/listicle**(CSDN·BuzzFeed·Society19) | **能读全**(robots 友好) | 不需要 Codex | 各语存量大全 |

**不为它们准备账号(走批量,省登录态)**:韩国 wiki(dump)、Reddit 存量(Arctic Shift/Academic Torrents)、"N个大全"页(直抓)、聚合站(直抓)。

---

## 3. 局头角色词挖掘 ⭐（本轮核心）

### 3.1 为什么角色词难挖、又为什么是矿脉入口

- 角色词是圈内黑话,**翻译不到**("局头"不是任何英文的译文,是各文化独立长出来的本地把手)。
- 但它是**内容生产者的自我标签**,顺角色词就能找到创作者和最密的语料。
- 三种词轴,组合起来撬开整个本地词簇:**场合词(局) × 角色词(局头) × 罚则词(大冒险)**。
- **重要观察**:多数文化里"局头"其实裂成两个子角色,**两个都要挖**:
  - **带局/主持人(MC)** → 产出手把手的玩法教程内容;
  - **气氛担当/社牛(personality)** → 产出氛围向、易爆火的内容。

### 3.2 挖词方法（6 法;Codex 每进一个新语区,先跑这套再采集）

1. **平台自补全 = 黑话词神谕(最强)**:在平台搜索框敲粗糙种子词,收割自动补全 + 相关搜索/"大家还在搜" + 结果帖上共现的 hashtag。"局头"就是这么被找到的——平台自己告诉你真人在搜什么。
   - **滚雪球**:拉种子标签下 20 条帖 → 抽所有 hashtag → 按频次排序 → 排名靠前的陌生词 = 下一批种子 → 迭代。
2. **锚场合词×角色词×罚则词**,别锚"游戏"。场合词(previa/apéro/회식/MT/barkada/inuman)往往是真正入口。
3. **维基跨语言链接**:打开 "drinking game" 词条 → 左侧语言栏 → 每种语言标题就是本地统称。나무위키分类名给韩国民间叫法。
4. **AI 出题、搜索量判卷(直接解"喂 AI 找不到")**:让 AI 生成候选(翻译+可能俚语+词形变体+场合×角色×罚则组合)→ 每个候选拿去平台搜,**有真实结果量的是活标签,搜不出的是死词**。
5. **反查创作者 + 商品标题**:一个高产垂类创作者的标题标签 = 本地词库;电商/应用商店"喝酒卡牌+该语言"的商品标题用的都是黑话(tagay cards/walwal/Sbronzopoli 都是这么冒出来的)。
6. **社区/评论/语言问答**:"你们那儿管这个叫什么" + 读评论区。

### 3.3 局头跨语言种子词表（现成;✓=已验证,其余为候选,Codex 按方法④逐个验搜索量）

| 语区 | 局（场合词） | 局头（角色词/带局人） | 游戏统称 | 罚则/大冒险词 |
|---|---|---|---|---|
| **中文** ✓ | 酒局/局/homebar局/K歌局 | **局头**/气氛组/社牛/带节奏的 | 喝酒游戏/酒桌游戏/团建/破冰 | 整蛊/整人/大冒险/惩罚 |
| **韩** ✓ | 회식/MT/술자리 | **사회자·주최자**(带局) · **분위기메이커·인싸**(气氛) | 술게임/순발력게임 | 벌칙/벌주 · 권주가 |
| 英(美) | pregame/kickback/function | host/MC/**hype man**/ringleader/**social chair**(社团)/instigator | drinking games/party games | dares/truth or drink/forfeits |
| 英(英) | pres/pre-drinks | host/MC/the one who runs pres | uni drinking games | dares/forfeits |
| 英(菲) | inuman/tagay/barkada | **emcee**/host/"life of the party" | parlor games/larong pinoy | walwal/dares · walwal cards |
| 法 | apéro/soirée | animateur/**maître du jeu**/boute-en-train | jeux à boire/jeux d'apéro | gages/défis |
| 意 | serata/aperitivo | animatore/**mattatore**/mazziere(发牌者) | giochi da bere/giochi alcolici | penitenze |
| 西 | previa/carrete(智利) | animador/**el alma de la fiesta** | juegos para tomar/de previa | castigos/retos |
| 葡(巴) | esquenta/rolê | animador/**o puxador**(带头者) | jogos para beber/brincadeiras | prendas/castigos |
| 德 | Vorglühen | Spielleiter/**Stimmungskanone**(气氛炮) | Trinkspiele/Partyspiele | Strafe |
| 日 | 飲み会/宴会 | 幹事(organizer)/**盛り上げ役**(气氛担当)/MC | 飲みゲーム/宴会ゲーム | 罰ゲーム |

> 用法:每个语区,先用"角色词"这一列去平台搜验哪个是活标签,活的那个就顺着它找创作者和内容;死的丢弃。中文"局头"、韩语"사회자/분위기메이커"已确认可用。

---

## 4. 采集双轨计划（并行,别让批量排在活体后面等）

### 轨 A — 活体登录扫（你的账号 + Codex,串行,按价值排序）

| 顺序 | 平台 | 登录态 | 覆盖大头 | 采什么 |
|---|---|---|---|---|
| P0 | 小红书 | 你的号 | 华语绝对大头 | 局头/喝酒游戏/团建/破冰/homebar/K歌局 |
| P1 | 抖音 | 你的号 | 华语视频层 | 同上 + 口播玩法 |
| P1 | TikTok | 一个号多语扫 | 英西意法葡德 | 见下方语言簇表 |
| P2 | Instagram | 你的号 | 印度 antakshari/kitty | Reels + 视觉层 |
| P2 | Facebook | 你的号 | 菲律宾 parlor games | 社区帖 + 评论 |
| P3 | YouTube | 可选 | 韩国 술게임 demo | 长视频 + 字幕 |

**TikTok 多语簇（搜索驱动,不刷 For You）**：美区剥离后不是独立内容库,搜索/hashtag 按**查询语言**走,一个账号搜 native 词根即可覆盖多语。

| 语言 | 词根 hashtag | 覆盖文化 |
|---|---|---|
| EN | party games / drinking games / pres games / Pinoy party games / parlor games | 美英菲印澳尼 |
| ES | juegos para tomar / juegos de previa / juegos para fiestas | 西墨阿哥智 |
| IT | giochi da bere / giochi alcolici | 意大利 |
| FR | jeux à boire / jeux d'apéro / jeux de soirée | 法比魁 |
| PT | jogos para beber / brincadeiras | 巴西葡 |
| DE | Trinkspiele / Partyspiele | 德奥 |

> 活体轨重点是 delta:视频口播 + **评论区变体** + `2026/viral/変わり種`。骨架已有,别重抓清单。

### 轨 B — 批量下载（不登号,Codex 后台脚本,与轨 A 同时跑）

1. **나무위키 dump**(韩国 술게임整库)+ HuggingFace `heegyu/namuwiki-extracted`(已清洗)。授权 CC BY-NC-SA:抽机制事实+改写,别搬成段原文。
2. **维基百科各语种 dump**(抽 drinking game / party game 条目 + 跨语言标题当本地统称)。
3. **Common Crawl** 抽 listicle 站(已爬好的公开网,不碰活站)。
4. **Reddit** Arctic Shift(月度免费存档,Pushshift 继任)+ Academic Torrents(2005–2025 全量帖+评论)。**不买号、不付费 API**。
5. **各语"大全"页直抓**(108大全 / 215 NHIE / 40 jeux / 菲 tagay cards / 意 giochi lists / 印 kitty lists)。

---

## 5. 开工顺序

**P0 同时启动**:小红书活体(轨 A)‖ 全部轨 B 下载脚本 kick off
→ **P1**:抖音 ‖ TikTok(先 EN/ES/IT/FR)
→ **P2**:IG(印度)+ FB(菲律宾)‖ 轨 B 解析去重
→ **P3**:Tier 2 语言(越南/泰国/印尼/波兰/尼日利亚)TikTok 补扫 + YouTube 韩国 demo。

**文化大国清单**(活体层):
- 已覆盖(机制层):中日韩/美英/德法/西/巴墨/俄土/北欧/东南亚/澳新
- Tier 1 新增:**菲律宾**(FB+TikTok,parlor games)、**印度**(IG+Pinterest,antakshari,是派对不是饮酒)、**意大利**(giochi da bere)
- Tier 2:越南、泰国、印尼、波兰、尼日利亚

---

## 6. 采集纪律（4 条红线,每个任务都带上）

1. **只抽机制原子 + 改写入库,不复制原文文案**,来源链接逐条留档(UGC 版权;dump 的 CC-NC 授权同理)。
2. **强制饮酒原子** → 照录 + 强制打**非酒替代/档位钳**标注(**韩语料钳位最严**,其强制劝酒文化最重)。
3. **亲密/羞辱类**(异性摸头、模拟求婚、m/s 梗、亲亲、公主抱)→ **黄暴档 + 可拒钳制**。
4. **屏幕自指 / 摄像头 / BGM 类** → 单列,喂 05 册对应子类。
5. **账号安全**:小红书/抖音用**你本人真号**、慢速拟人、别上签名接口;不用闲鱼 burner 号采主战场(采两下就封)。

---

## 7. 新原子类（这轮浮现,单列入库）

- **屏幕自指原子**(手机举 1-10 打分 / 两秒识曲 / notes 计分 / 拍照做梗 / 美杜莎对视 / "手机上写名字只用是否问")——全球,界面天生能玩。
- **BGM / 口号原子**(韩国 권주가,每个游戏自带音乐 intro)——fx 原语富矿,建议单开子类。
- **歌曲接龙原子**(印度 antakshari + 爱尔兰 Rattlin' Bog + 韩国)——跨文化新家族。
- **"改造现有游戏 + 饮酒层"生成模式**(意大利 Sbronzopoli/醉酒大富翁、drinking chess、drinking battleship;Pinterest 电影 drinking game)——这是**生成规则**不是单条,喂 `$gen` 引擎。
- **parlor game 原子**(菲律宾物理派对游戏:Apakan Lobo/踩气球、Basagan ng Itlog/砸蛋、Kiskisan)——命运/表演类。

---

## 8. 关键事实备忘（实测,防走弯路）

- **小红书直连**:robots + 权限双封,外部工具进不去 → 靠你的登录态。
- **TikTok**:robots 禁抓正文,看不了视频/评论 → 靠 Codex 浏览器+转写+读评论;搜索按查询语言走,一个号多语覆盖。
- **抖音**:与国际版彻底隔离的中国专用 App,是真"独立区" → 单独走国内号。
- **美区 TikTok**(2026-01 剥离归 USDS Joint Venture):**不是独立 App、不是独立内容库**,后端换所有权,For You 偏美国话题,但**搜索/hashtag 不受区域锁**。
- **Reddit**:免费层 100 QPM 仅非商业且需预审批;商用 $0.24/1K、$12k/年起步(过度配置)→ 走 Arctic Shift + Academic Torrents **免费全量**。闲鱼卖的"Reddit 抓取 API 从 2006 起"大概率是免费存档重新打包,**别买**;卖号对只读采集无用。
- **나무위키**:官方发 DB dump,且明确"爬站添负担,请用 dump" → 走 dump。
- **"尽可能全"的正解**:整库 dump = 整个语料,远胜任何爬取;活体长尾才用登录态。**最全的采集是下载来的,不是爬来的。**

---

## 9. 挂起中的相邻决策（非采集,备查）

- 感知三档已排期:**摇一摇即刻施工**(§20,一天级,挂真人局软前置)、**环境拍照导入** → 产品壳 backlog、**AI 视觉/手机功能道具** → v2.3(判级 0 解掉前不发摄像机)。
- API 申请路线(TikTok Research API / Meta Content Library)因耽误进度暂缓,回到登录态主采。
