# Facebook 菲律宾原生补扫：Pinoy parlor games 与 barkada party games（2026-07-16）

## 结论

Facebook 菲律宾方向的登录态原生搜索已经打通。两条精准 Reels 查询都能返回相关内容，但密度明显不同：`Pinoy parlor games`前排人工可见 4 条，4/4 相关；`barkada party games`可见 2 条，2/2 广义相关，其中一条是商业桌游场馆内容，核心派对玩法只有 1 条。

本轮打开了两条固定视频详情。页面能提供稳定链接、标题、场景载体和可见互动指标，但都没有暴露足够完整的规则或转写，因此不补猜机制，也不写入玩法原子库。

## 查询审计

| 查询 | 可见样本 | 相关性 | 判定 |
| --- | ---: | --- | --- |
| `Pinoy parlor games` | 4 条 | 4/4 相关 | 本轮高密度入口 |
| `barkada party games` | 2 条 | 2/2 广义相关；1 条核心玩法、1 条商业噪声 | 可用但需要过滤桌游与场馆内容 |

采集表面为登录态 Facebook Reels 搜索：

- [Pinoy parlor games 搜索](https://www.facebook.com/search/videos/?q=Pinoy%20parlor%20games)
- [barkada party games 搜索](https://www.facebook.com/search/videos/?q=barkada%20party%20games)

这些数字只描述等待页面加载后人工可见的前排样本，不是平台总量或全站相关率。

## `Pinoy parlor games`：4/4 相关

前排可见结果如下：

| 公开页面或创作者 | 可见标题 | 时长 | 日期 | 搜索页观看量 | 固定链接 |
| --- | --- | ---: | --- | ---: | --- |
| Top Pinoy Moments | Ranking Pinoy Parlor Games Funny Moments | 1:10 | 2025-02-23 | 1.2M | [视频](https://www.facebook.com/watch/?v=672723811987684) |
| Jinky Lira Babad | Funny Pinoy Parlor Games | 1:54 | 2025-01-30 | 4K | [视频](https://www.facebook.com/watch/?v=638997451912484) |
| Pinoy's Funniest Videos | 搜索页未记录标题 | 0:15 | 2025-06-18 | 6.7K | [视频](https://www.facebook.com/watch/?v=737527838933206) |
| Ghee-Emz Pakulo & Palaro | 搜索页未记录标题 | 0:35 | 2023-03-14 | 13K | [视频](https://www.facebook.com/watch/?v=934647504220696) |

### 固定页详情

[Ranking Pinoy Parlor Games Funny Moments](https://www.facebook.com/watch/?v=672723811987684)详情页可见时长 1:10、79K reactions、145 comments 和 1.9M views，标题带有`#parlorgames`、`#partygames`、`#Philippines`。

画面可确认户外多人实体活动、气球道具和 ranking 呈现层，但没有足以重建完整规则的连续信息。评论只看到情绪或 @ 式信号，没有可用的规则解释；本仓不保存评论者身份。因此这条视频只作为内容载体和查询密度证据，不进入玩法原子库。

## `barkada party games`：相关但有商业噪声

前排两条结果为：

- Dailiavlogs 的[Jack en Poy Barkada edition / Barkada Christmas Party Games! Jack en Poy ubusan ng Lahi!](https://www.facebook.com/watch/?v=630802402608235)，2:37，2024-12-16，搜索页可见 4.6K views；
- WhenInManila / All Aboard XP 的[商业桌游场馆 Reel](https://www.facebook.com/reel/2069971316536684/)，0:20，2023-02-19。

第二条虽然与朋友聚会广义相关，但属于商业桌游/场馆内容，说明`barkada`作为朋友圈语境词会扩大召回范围，不能直接等同于本土 parlor-game 玩法词。

### 固定页详情

[Jack en Poy Barkada edition](https://www.facebook.com/watch/?v=630802402608235)详情页可见时长 2:37、19 reactions、4 comments 和 8.5K views。画面可确认室内多人聚会和气球道具；标题只能支持 Jack en Poy、barkada、Christmas party 和淘汰主题的内容包装。

本轮没有完整转写或连续规则，不把标题中的`ubusan ng Lahi`扩写成未观察到的操作步骤。公开评论只支持朋友同玩、礼物或聚会情境，没有给出完整规则；评论者身份未保存。这条同样不进入玩法原子库。

## 对“河流地图”的作用

- 第一层入口优先使用`Pinoy parlor games`，它在本轮前排样本中密度最高；
- 第二层可用`barkada party games`扩到朋友聚会语境，但要过滤商业桌游、场馆和商品内容；
- Facebook 更适合收集可固定的视频、页面连载、标题包装和公开评论长尾；规则不完整时只能保留发现线索，不能凭画面补写玩法；
- 后续若要抽 atoms，应优先寻找有完整主持讲解、清晰字幕或多来源互证的固定页。

## 数据边界

本轮全程只读，没有点赞、反应、关注、加群、评论、私信或分享，也没有进入私人群。仓库仅保存公开页面/创作者标签、固定公开链接、标题、日期、时长、页面可见指标、场景级信号和评论语义转述；不保存评论者身份、私人账号标识、会话参数、Cookie、令牌、截图或长评论。
