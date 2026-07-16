# 轨 B 批量来源清单

核验日期：2026-07-16（Asia/Shanghai）

配套机器清单：[`batch_sources.json`](./batch_sources.json)。JSON 可直接交给 `run_ugc_collection.py batch`；本文解释来源、许可、体积、风险和启用理由。

## 结论先行

- 共登记 **36 个入口**，覆盖 6 个来源族：Namuwiki/Hugging Face、Wikimedia dumps、Common Crawl、Arctic Shift、Academic Torrents、公开多语 listicle。
- 默认只启用 **17 个小探针**：6 个目录/API/详情探针，加 11 个单页公开 HTML。2.20 GiB～3.97 TiB 的全量、WARC 和 torrent payload 全部关闭。
- “能公开访问”不等于“开放许可”。Common Crawl、Arctic Shift、Academic Torrents 和普通 listicle 都不能被当作一揽子开放内容库。
- 原始 HTML、parquet、XML、WARC、Reddit JSONL 只进忽略缓存；仓库只保留来源 URL、极短证据说明和改写后的机制事实。
- 多语翻译页只用于生成候选词，不能拿 7 个翻译页面冒充 7 个独立文化证据。

## 状态含义

| 状态 | 含义 |
|---|---|
| `ready_probe` | 当前已核验可访问，体量有界，默认可跑。 |
| `disabled_selective` | 文件不算巨型，但只有在明确语言/查询目标后才打开。 |
| `disabled_large` | 大文件或打开描述符会触发大传输，默认关闭。 |
| `disabled_large_rights_review` | 同时存在显著体积与许可约束。 |
| `disabled_critical*` | TB 级或高隐私/删除内容风险，只能人工审批和选择性取文件。 |

## 默认运行边界

默认探针并发运行命令：

```powershell
python run_ugc_collection.py batch `
  --manifest research/ugc/batch_sources.json `
  --cache-dir .ugc-cache/batch `
  --jobs 4 `
  --resume
```

这条命令不会拉取 Namuwiki parquet、Wikimedia 正文 dump 或任何 torrent payload。Academic Torrents 默认只取详情 HTML；JSON 中的 `.torrent` 条目仍是 `enabled: false`。

## 1. Namuwiki / Hugging Face

| 条目 | 当前原始 URL | 体积 | 许可 | 状态与风险 |
|---|---|---:|---|---|
| 当前规模探针 | [Dataset Viewer `/size`](https://datasets-server.huggingface.co/size?dataset=heegyu%2Fnamuwiki-extracted) | 699 B 响应 | 数据集卡声明 CC BY-NC-SA 2.0 KR | `ready_probe`；只取元数据。 |
| 完整 parquet | [官方 Hub parquet 转换文件](https://huggingface.co/datasets/heegyu/namuwiki-extracted/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet) | 2,357,235,583 B（2.20 GiB）；565,293 行；估算解码内存 9,701,946,000 B | [CC BY-NC-SA 2.0 KR](https://huggingface.co/datasets/heegyu/namuwiki-extracted/blob/main/LICENSE.md) | `disabled_large_rights_review`；非商业、署名、相同方式共享。 |

核验依据：[数据集主页](https://huggingface.co/datasets/heegyu/namuwiki-extracted)、[parquet 清单 API](https://datasets-server.huggingface.co/parquet?dataset=heegyu%2Fnamuwiki-extracted)、[规模 API](https://datasets-server.huggingface.co/size?dataset=heegyu%2Fnamuwiki-extracted)。

使用判断：

- 这是历史快照，不是 2026 年实时 Namuwiki；适合挖韩国游戏条目、别名和角色/惩罚词根，不适合证明“今天仍然流行”。
- 数据里有正文和贡献者信息。不要把贡献者列表、原文长段或疑似个人信息带进结果库。
- “抽取事实再改写”并不会自动消灭许可义务。任何可能构成改编或再分发的产物仍须做 CC BY-NC-SA 审查；商业用途默认禁止。

## 2. Wikimedia dumps

官方入口：[Wikimedia dump 总索引](https://dumps.wikimedia.org/backup-index.html)、[可下载内容说明](https://meta.wikimedia.org/wiki/Data_dumps/What%27s_available_for_download)。当前文本通常按 [CC BY-SA 4.0 与 GFDL](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use#7._Licensing_of_Content) 提供，但导入文本、个别项目和非文本媒体可能例外，应回查页面页脚、历史和讨论页。

### 2.1 韩国正文与探针

| 条目 | 当前原始 URL | 2026-07-16 HEAD 体积 | 状态 |
|---|---|---:|---|
| 校验清单 | [`kowiki-latest-sha1sums.txt`](https://dumps.wikimedia.org/kowiki/latest/kowiki-latest-sha1sums.txt) | 13,600 B | `ready_probe` |
| 主命名空间标题 | [`kowiki-latest-all-titles-in-ns0.gz`](https://dumps.wikimedia.org/kowiki/latest/kowiki-latest-all-titles-in-ns0.gz) | 8,646,363 B | `disabled_selective` |
| 当前文章正文 | [`kowiki-latest-pages-articles.xml.bz2`](https://dumps.wikimedia.org/kowiki/latest/kowiki-latest-pages-articles.xml.bz2) | 1,316,647,609 B | `disabled_large` |

### 2.2 跨语种标题词典

标题 dump 的用途是发现本地条目名、拼写和词形，不是验证真实使用量。所有 URL 都是 Wikimedia 官方 `latest` 别名；生产跑必须从校验清单记录实际日期和散列。

| 语言 | 项目 | 当前原始 URL | 压缩体积 | 状态 |
|---|---|---|---:|---|
| 英语 | `enwiki` | [标题 dump](https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-all-titles-in-ns0.gz) | 108,706,389 B | `disabled_large` |
| 德语 | `dewiki` | [标题 dump](https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-all-titles-in-ns0.gz) | 29,501,372 B | `disabled_selective` |
| 西班牙语 | `eswiki` | [标题 dump](https://dumps.wikimedia.org/eswiki/latest/eswiki-latest-all-titles-in-ns0.gz) | 23,744,781 B | `disabled_selective` |
| 法语 | `frwiki` | [标题 dump](https://dumps.wikimedia.org/frwiki/latest/frwiki-latest-all-titles-in-ns0.gz) | 27,587,611 B | `disabled_selective` |
| 意大利语 | `itwiki` | [标题 dump](https://dumps.wikimedia.org/itwiki/latest/itwiki-latest-all-titles-in-ns0.gz) | 18,049,554 B | `disabled_selective` |
| 葡萄牙语 | `ptwiki` | [标题 dump](https://dumps.wikimedia.org/ptwiki/latest/ptwiki-latest-all-titles-in-ns0.gz) | 12,134,708 B | `disabled_selective` |
| 日语 | `jawiki` | [标题 dump](https://dumps.wikimedia.org/jawiki/latest/jawiki-latest-all-titles-in-ns0.gz) | 15,749,518 B | `disabled_selective` |
| 他加禄语 | `tlwiki` | [标题 dump](https://dumps.wikimedia.org/tlwiki/latest/tlwiki-latest-all-titles-in-ns0.gz) | 988,453 B | `disabled_selective` |
| 印地语 | `hiwiki` | [标题 dump](https://dumps.wikimedia.org/hiwiki/latest/hiwiki-latest-all-titles-in-ns0.gz) | 1,995,710 B | `disabled_selective` |
| 越南语 | `viwiki` | [标题 dump](https://dumps.wikimedia.org/viwiki/latest/viwiki-latest-all-titles-in-ns0.gz) | 9,723,871 B | `disabled_selective` |
| 泰语 | `thwiki` | [标题 dump](https://dumps.wikimedia.org/thwiki/latest/thwiki-latest-all-titles-in-ns0.gz) | 3,293,378 B | `disabled_selective` |
| 印尼语 | `idwiki` | [标题 dump](https://dumps.wikimedia.org/idwiki/latest/idwiki-latest-all-titles-in-ns0.gz) | 8,912,219 B | `disabled_selective` |
| 波兰语 | `plwiki` | [标题 dump](https://dumps.wikimedia.org/plwiki/latest/plwiki-latest-all-titles-in-ns0.gz) | 13,981,755 B | `disabled_selective` |

主要风险：

- `latest` 会变化，不能当可复现实验 ID；启用后先固定日期版 URL和 SHA1。
- 标题只有“某条目存在”，不说明词是否是派对圈黑话，更不说明 2026 年搜索热度。
- 署名最稳妥的方式是保留原页面 URL或可回溯作者历史的稳定 URL；不能只写“来源：Wikipedia”。

## 3. Common Crawl

| 条目 | 当前官方 URL | 体积 | 许可 | 状态与风险 |
|---|---|---:|---|---|
| 全部抓取索引目录 | [`collinfo.json`](https://index.commoncrawl.org/collinfo.json) | 34,403 B | [Common Crawl Terms of Use](https://commoncrawl.org/terms-of-use)；原站内容仍受各自版权和条款约束 | `ready_probe` |
| 当前最新集合 | `CC-MAIN-2026-25`（June 2026 Index） | 整体为 PB 级仓库 | 同上 | 只查询 URL 索引，不整包下载 |

官方技术说明：[CDXJ Index](https://commoncrawl.org/cdxj-index)、[Latest Crawl](https://commoncrawl.org/latest-crawl)、[数据概览](https://commoncrawl.org/overview)。

正确的单页取法：

1. 对已知原始页面查询当前索引：

   ```text
   https://index.commoncrawl.org/CC-MAIN-2026-25-index?url=<URL-ENCODED-ORIGIN-URL>&output=json&filter=status:200
   ```

2. 从 CDXJ 结果记录 `filename`、`offset`、`length`、`digest`、`timestamp`。
3. 只对 `https://data.commoncrawl.org/<filename>` 发出 `Range: bytes=offset-(offset+length-1)`。
4. 解出该条 WARC response，保留原始站 URL和抓取时间，不把 Common Crawl URL 当成作者来源。

当前 `batch` 下载器不能为单条 source 表达任意 HTTP Range，所以清单故意只启用 `collinfo.json`，没有放入任何整条 WARC URL。直接下载 WARC 往往会为一页内容拉数 GiB，是错误路径。

许可红线：Common Crawl 只提供访问服务的有限许可；它明确提醒 Crawled Content 可能受原权利人的独立条款约束。只抓公开 listicle 的机制事实、短证据和 URL，不复制整篇正文，不把抓取库用作无权利审查的训练语料。

## 4. Reddit：Arctic Shift 与 Academic Torrents

### 4.1 Arctic Shift

| 条目 | 当前原始 URL | 体积 | 许可 | 状态与风险 |
|---|---|---:|---|---|
| `r/DrinkingGames` 有界 API 探针 | [subreddit search，`limit=1`](https://arctic-shift.photon-reddit.com/api/subreddits/search?subreddit=DrinkingGames&limit=1&fields=display_name%2Csubscribers) | 单条 JSON | 存档没有声明一揽子数据许可；Reddit 内容权利仍属于用户 | `ready_probe` |
| 官方下载链接清单 | [原仓库 `download_links.md`](https://github.com/ArthurHeitmann/arctic_shift/blob/master/download_links.md) / [raw](https://raw.githubusercontent.com/ArthurHeitmann/arctic_shift/master/download_links.md) | 10,458 B | 仓库未见 LICENSE；只当发现元数据 | `ready_probe` |

项目原始文档：[Arctic Shift 仓库](https://github.com/ArthurHeitmann/arctic_shift)、[API 文档](https://github.com/ArthurHeitmann/arctic_shift/tree/master/api)、[文件内容说明](https://github.com/ArthurHeitmann/arctic_shift/blob/master/file_content_explanations.md)、[小 subreddit/user 下载工具](https://arctic-shift.photon-reddit.com/download-tool)。

已核验的服务约束：

- API 明示无 uptime/performance 保证；普通用户每秒少量请求一般可用，过量会限流。
- 搜索端点上限通常为 100；清单把探针固定为 `limit=1` 且仅取两个字段。
- 文档建议批量处理走 dump，不要压 API；这不等于授权批量再利用。
- 2023-04 之后的数据说明称由官方 Reddit API 获取；更早部分含 Pushshift 历史存档。
- 归档可能包含后来删除或修改的内容。必须有删除/移除处理，不能因“存档里还在”就重新公开。

Reddit 官方 [Data API Terms](https://redditinc.com/policies/data-api-terms) 明确说 User Content 归用户所有，并限制未获权利人明确许可的 AI/ML 训练用途。Arctic Shift 是第三方存档，不会替代这些权利和义务。

### 4.2 Academic Torrents

| 数据集 | 当前原始页 / torrent | 声明体积 | 数据集许可字段 | 状态 |
|---|---|---:|---|---|
| 2026-06 月包 | [详情](https://academictorrents.com/details/3bac8bd352bbb74bbb23df4273cf3da5d66ee5a5) / [torrent](https://academictorrents.com/download/3bac8bd352bbb74bbb23df4273cf3da5d66ee5a5.torrent) | 70.38 GiB：comments 48.18 GiB；submissions 22.20 GiB | 空 | 详情页 `ready_probe`；torrent `disabled_large` |
| 2005-06～2025-12 月包 | [详情](https://academictorrents.com/details/3d426c47c767d40f82c7ef0f47c3acacedd2bf44) / [torrent](https://academictorrents.com/download/3d426c47c767d40f82c7ef0f47c3acacedd2bf44.torrent) | 3.80 TiB，488 文件 | 空 | `disabled_critical` |
| 2005-06～2025-12 按 subreddit 拆分 | [详情](https://academictorrents.com/details/3e3f64dee22dc304cdd2546254ca1f8e8ae542b4) / [torrent](https://academictorrents.com/download/3e3f64dee22dc304cdd2546254ca1f8e8ae542b4.torrent) | 3.97 TiB，79,955 文件 | 空 | `disabled_critical_selective_only` |

2026-06 原始发布页还给出了 payload SHA256：[Arctic Shift June 2026 release](https://github.com/ArthurHeitmann/arctic_shift/releases/tag/2026_06)。

- `RC_2026-06.zst`: `2bb00d6677f32cd4974d2cc1f4ae0f9d39c63aed61dedfd2cfe663dbf070b912`
- `RS_2026-06.zst`: `4aa6978212825c5ae81d32517ed88c7fad6abc2682bac9c572a42592e56cf781`

Academic Torrents 的[平台条款](https://academictorrents.com/terms.php)只说上传者必须有合法分享权、服务按现状提供；它没有替每个数据集补一张开放许可证。上述三个 Reddit 数据集的 `license` 元数据均为空，不能标成“免费可随便用”。

选择建议：若后续确有 Reddit 批量需求，只考虑按 subreddit 拆分的 torrent，并在 torrent 客户端里只勾选明确命名的小社区；绝不选择 79,955 个文件全量。先做空间配额、删除请求、个人信息、NSFW 和原文保留期限审查。

## 5. 公开多语 listicle

### 5.1 平行多语词根探针

同一篇 PsyCat Games listicle 有多个语言版本。它们适合做“同一机制在不同语言里怎样命名”的平行词典，但属于同一出版源，不能给每个语言版本各算一次独立证据。其 [Terms of Service](https://psycatgames.com/terms-of-service/) 明示相关知识产权由站点及许可方所有，没有开放内容许可。

| 语言 | 当前原始页面 | 状态 | 使用限制 |
|---|---|---|---|
| 英语 | [Drinking Games for Students](https://psycatgames.com/magazine/party-games/student-drinking-games/) | `ready_probe` | 只取候选名称、结构化机制事实；不复制段落 |
| 德语 | [德语版](https://psycatgames.com/de/magazine/party-games/student-drinking-games/) | `ready_probe` | 平行翻译，不算本地独立证据 |
| 西班牙语 | [西语版](https://psycatgames.com/es/magazine/party-games/student-drinking-games/) | `ready_probe` | 同上 |
| 法语 | [法语版](https://psycatgames.com/fr/magazine/party-games/student-drinking-games/) | `ready_probe` | 同上 |
| 意大利语 | [意语版](https://psycatgames.com/it/magazine/party-games/student-drinking-games/) | `ready_probe` | 同上 |
| 葡萄牙语 | [葡语版](https://psycatgames.com/pt/magazine/party-games/student-drinking-games/) | `ready_probe` | 同上 |
| 波兰语 | [波兰语版](https://psycatgames.com/pl/magazine/party-games/student-drinking-games/) | `ready_probe` | 同上 |

这些页面是“小体量安全探针”，但仍有两类风险：编辑正文受版权保护；饮酒、强制、羞辱或成人问题不能原样带入产品。每个机制必须同时生成无酒精替代、跳过权和危险动作过滤。

### 5.2 菲律宾与印度的独立文化入口

| 文化/语言 | 当前原始页面 | 已核验体积 | 状态 | 价值与风险 |
|---|---|---:|---|---|
| 菲律宾，英语/本地词 | [Philippine Primer: Common Filipino Party Games](https://primer.com.ph/tips-guides/2016/12/26/common-filipino-party-games/) | 单页 HTML | `ready_probe` | 本地出版入口；仍受出版者版权约束 |
| 菲律宾，英语/本地词 | [Staycations.ph: Ultimate List of Pinoy Parlor Games](https://staycations.ph/ultimate-list-of-pinoy-parlor-games/) | 125,059 B | `ready_probe` | 可抽 `Pinoy parlor games`、玩法名和材料；需第二来源复核 |
| 印度，英语/本地词 | [Partie: Best Party Games for Adults in India](https://partie.in/blog/best-party-games-for-adults-india) | 153,812 B | `ready_probe` | 含 Antakshari、Dumb Charades 等本地线索；也是产品营销页 |
| 印度，英语/本地词 | [FirstCry: Kitty Party Games](https://parenting.firstcry.com/articles/25-fun-kitty-party-games-ideas/) | 单页 HTML | `ready_probe` | 提供 kitty party 场景和玩法；注意性别刻板印象与版权 |

这四页都没有开放许可声明。公开访问只支持把它们作为研究入口；缓存应私有，输出只保留短证据说明、URL 和改写后的事实。

## 6. 用轨 B 找“局头”类本地角色词

批量源不是让 AI 直接翻译“局头”，而是用共现和独立复核找本地黑话：

1. 从 Wikimedia 标题、Namuwiki 条目名和多语平行页抽取“场合词、主持动作、气氛动作、惩罚词”的本地词形。
2. 在 listicle 的标题、目录、规则主语里找“谁解释规则、谁开局、谁决定惩罚、谁把气氛带起来”的名词和动词。
3. 把主持/带局角色与气氛担当分成两列；不要强行要求每种文化只有一个词。
4. 用独立本地出版页或 Arctic Shift 的有界社区查询复核。平行翻译页只产生候选，不升级 `verified`。
5. 一个角色词至少要满足：两处独立来源；其中至少一处是本地原创/社区语境；能够指向实际行为，不只是字典直译。
6. 最终记录只写短改写，例如“由 X 解释规则并决定下一轮”，不要搬运原段落、问题清单或评论。

推荐证据等级：

| 等级 | 条件 | 可做什么 |
|---|---|---|
| Seed | 一处标题、翻译或 AI 候选 | 继续搜索，不入已验证词库 |
| Candidate | 一处本地原创页 + 可解释行为 | 进入候选词表 |
| Verified | 两处独立本地/社区来源，且至少一处有规则或上下文 | 可作为角色词入口驱动后续采集 |

## 7. 开启大源前的检查表

- 固定 dated URL、文件体积和 SHA256/SHA1；不要只记 `latest`。
- 设下载上限、磁盘预算和展开后内存预算。
- 明确许可是否允许当前用途；“无许可证”按不授予再利用权处理。
- 对 Reddit/UGC 做个人信息最小化、删除请求、NSFW 和未成年人内容过滤。
- torrent 只选明确文件；WARC 只取明确 byte range；不允许全选。
- 原始缓存保持在 `.ugc-cache`；不得提交原文、HTML、dump、torrent payload。
- 每条产出保留原始 URL、抓取时间、查询词和简短证据说明。
- 饮酒机制必须附无酒精替代；危险、胁迫、羞辱和成人内容进入安全分级，不原样复现。

## 8. 当前未列为可执行源的入口

- Namuwiki 实时站：机器人防护和动态访问不稳定；本轮只登记可核验的 Hugging Face 快照。
- Common Crawl 整 WARC：当前 downloader 不支持任意 Range，故不把整包 URL 塞进 manifest。
- Reddit 全量：3.80～3.97 TiB 且数据集许可字段为空，保持关闭。
- 403、登录墙或本轮探测超时的 listicle：不以“可能能抓”为由加入默认源；后续重新核验后再增补。
