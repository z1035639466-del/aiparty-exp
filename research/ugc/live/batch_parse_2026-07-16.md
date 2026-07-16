# Track B 小探针解析报告（2026-07-16）

## 结论

- 已核对启用并下载的探针：**17/17**。
- 可支持机制的内容页：**11**；仅含入口/体量/校验元数据：**6**。
- 原始候选：**75**；按机制去重后：**29**；合并重复：**46**。
- 去重后仍保留 **11** 个来源 URL。
- 涉及原始饮酒规则并已加无酒精替代的原子：**7**；黄/成人分级：**1**。

六个语料入口探针没有正文，因此没有被包装成玩法证据。所有内容页只用短标记核验，提交内容均为中文机制改写，不保存网页段落、例题或字幕。

## 来源覆盖

| 探针 | 类型 | 状态 | 字节 | 支持原子 | 说明 |
|---|---|---|---:|---:|---|
| [hf_namuwiki_size_probe](https://datasets-server.huggingface.co/size?dataset=heegyu%2Fnamuwiki-extracted) | huggingface | metadata_only | 699 | 0 | 仅为 Hugging Face 数据集体量与行数元数据，未下载正文。 |
| [wikimedia_kowiki_checksum_probe](https://dumps.wikimedia.org/kowiki/latest/kowiki-latest-sha1sums.txt) | wikimedia | metadata_only | 13600 | 0 | 仅为 Wikimedia 校验和清单，不含百科正文。 |
| [commoncrawl_catalog_probe](https://index.commoncrawl.org/collinfo.json) | common_crawl | metadata_only | 34403 | 0 | 仅为 Common Crawl 集合目录，不含目标网页 WARC 内容。 |
| [arctic_shift_subreddit_probe](https://arctic-shift.photon-reddit.com/api/subreddits/search?fields=display_name%2Csubscribers&limit=1&subreddit=DrinkingGames) | arctic_shift | metadata_only | 63 | 0 | 仅返回子版块发现元数据，不含帖子或评论机制。 |
| [arctic_shift_download_index_probe](https://raw.githubusercontent.com/ArthurHeitmann/arctic_shift/master/download_links.md) | arctic_shift | metadata_only | 10458 | 0 | 仅为存档下载索引，不含 Reddit 帖子或评论正文。 |
| [academic_torrents_2026_06_details_probe](https://academictorrents.com/details/3bac8bd352bbb74bbb23df4273cf3da5d66ee5a5) | academic_torrents | metadata_only | 21174 | 0 | 仅为 70.38 GiB 月度数据集详情页，未打开 torrent payload。 |
| [listicle_psycat_en](https://psycatgames.com/magazine/party-games/student-drinking-games) | url_list | content_parsed | 61831 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_de](https://psycatgames.com/de/magazine/party-games/student-drinking-games) | url_list | content_parsed | 66668 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_es](https://psycatgames.com/es/magazine/party-games/student-drinking-games) | url_list | content_parsed | 65990 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_fr](https://psycatgames.com/fr/magazine/party-games/student-drinking-games) | url_list | content_parsed | 68088 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_it](https://psycatgames.com/it/magazine/party-games/student-drinking-games) | url_list | content_parsed | 65667 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_pt](https://psycatgames.com/pt/magazine/party-games/student-drinking-games) | url_list | content_parsed | 65194 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_psycat_pl](https://psycatgames.com/pl/magazine/party-games/student-drinking-games) | url_list | content_parsed | 66126 | 7 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_philippine_primer_party_games](https://primer.com.ph/tips-guides/2016/12/26/common-filipino-party-games) | url_list | content_parsed | 109974 | 5 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_ph_staycations_pinoy_parlor](https://staycations.ph/ultimate-list-of-pinoy-parlor-games) | url_list | content_parsed | 125059 | 8 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_india_partie_adult_party_games](https://partie.in/blog/best-party-games-for-adults-india) | url_list | content_parsed | 153812 | 6 | 短标记核验通过；只提交改写后的机制事实。 |
| [listicle_india_firstcry_kitty_party_games](https://parenting.firstcry.com/articles/25-fun-kitty-party-games-ideas) | url_list | content_parsed | 485241 | 7 | 短标记核验通过；只提交改写后的机制事实。 |

## 去重后机制

| ID | 标题 | 类型 | 来源数 | 饮酒护栏 | 分级 |
|---|---|---|---:|---|---|
| `ugc_0bdd26ee9843e887` | 花色字母抢词 | parlor_game | 7 | 是 | none |
| `ugc_12ffd3e93c5a9bca` | 胶带网格室内穿越 | parlor_game | 1 | 否 | none |
| `ugc_1a1e675b406934e6` | 积木抽取触发任务 | drinking_overlay | 7 | 是 | none |
| `ugc_1c72e1519a84559c` | 名人纸条双人表演猜词 | parlor_game | 1 | 否 | none |
| `ugc_35f870c5e1d7399d` | 低位奖券绳格 | parlor_game | 2 | 否 | none |
| `ugc_39e3d35d8bb98cab` | 四步翻牌预测 | drinking_overlay | 7 | 是 | none |
| `ugc_4a324d2be50557ef` | 手持勺青柠接力 | parlor_game | 1 | 否 | none |
| `ugc_4e9d6d2e727a6023` | 循环报点与匹配拍标 | parlor_game | 7 | 是 | none |
| `ugc_5825e60e96e28e28` | 口令人数快速成组 | parlor_game | 1 | 否 | none |
| `ugc_5f7850aacef90cbd` | 逐轮缩小舞蹈站位 | parlor_game | 2 | 否 | none |
| `ugc_61ef59ab83a35000` | 牌面碎片限时配对 | parlor_game | 1 | 否 | none |
| `ugc_665c598cbb5453de` | 背对背禁名描述 | parlor_game | 1 | 否 | none |
| `ugc_6a401cea76a10087` | 安全清单寻物 | parlor_game | 2 | 否 | none |
| `ugc_7575e2d483285cce` | 托盘物件限时回忆 | parlor_game | 1 | 否 | none |
| `ugc_78f4a71438bfd285` | 无声表演猜电影 | parlor_game | 1 | 否 | none |
| `ugc_7b43d1e94a61708e` | 本地文化图标宾果 | parlor_game | 1 | 否 | none |
| `ugc_7f49906af2fa1e05` | 指定词歌曲清单 | song_chain | 1 | 否 | none |
| `ugc_883a3dd79df63b5f` | 公开配料口味盲猜 | drinking_overlay | 7 | 是 | none |
| `ugc_8ffe5aaa81f778b2` | 逐句共创故事 | parlor_game | 1 | 否 | none |
| `ugc_9774649a97c33c9b` | 叙事主持切换昼夜阶段 | parlor_game | 1 | 否 | none |
| `ugc_9ba67940afdafa77` | 歌曲片段后定向问答 | bgm_chant | 1 | 否 | none |
| `ugc_9e7209fc628043d1` | 受限回复猜词 | parlor_game | 2 | 否 | none |
| `ugc_a51cb56752f6fc7c` | 英译歌名反向识别 | bgm_chant | 1 | 否 | none |
| `ugc_b017bb67b937bc08` | 音乐停顿定格 | parlor_game | 1 | 否 | none |
| `ugc_c652927a5a497548` | 结尾音节歌曲接龙 | song_chain | 1 | 否 | none |
| `ugc_ca02a64a2e7e732e` | 宝莱坞禁词描述 | parlor_game | 1 | 否 | none |
| `ugc_e396c020df1c83b5` | 自愿任务主持轮换 | parlor_game | 7 | 是 | yellow |
| `ugc_e669cea0342e1c9b` | 盖牌声明与质疑 | parlor_game | 1 | 否 | none |
| `ugc_f3d2375dbe1e5a6d` | 同点牌救援与目标转移 | drinking_overlay | 7 | 是 | none |

## 权利与安全边界

- 原始 HTML 只留在 `.ugc-cache`，不会提交到仓库。
- 公开可访问不等于开放许可；每个原子保留清单中的 URL 与 license/rights 说明。
- 多语镜像及跨站同机制会合并，但 `source_urls` 与来源标签不会丢失。
- 原规则若把饮酒当惩罚，记录仍标记 `forced_drinking=true`，同时强制提供积分、无酒精或跳过方案。
- 涉及陌生人任务、抢夺、口含器具、隐藏酒精、抱举或强迫接触的原结构均被拒绝或安全重写。

机器可读审计见 `batch_parse_2026-07-16.json`；机制库见 `../batch_atoms.jsonl`。
