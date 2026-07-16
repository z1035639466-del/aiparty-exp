# 受限批量探针实跑结果（2026-07-16）

## 结果

`batch_sources.json` 共 36 个来源：17 个小体量探针全部下载成功，19 个大文件/高风险入口保持禁用；缓存合计 1,414,047 字节。原始响应只存在被 `.gitignore` 排除的 `.ugc-cache/`，仓库仅提交来源清单、运行摘要和自行改写的事实。

首轮由于采集器没有发送可识别的请求标识，12 个站点返回 403/406。补充透明的项目 User-Agent、普通 `Accept` 与 `Accept-Language` 后，第二轮 17/17 成功；没有伪装登录、绕验证码、使用代理或突破反爬挑战。

## 已成功的探针

| 组 | 数量 | 内容 |
| --- | ---: | --- |
| 语料元数据 | 3 | Hugging Face Namuwiki 大小、韩文维基校验表、Common Crawl 集合目录 |
| Reddit 存档入口 | 3 | Arctic Shift 子版探针、下载索引、Academic Torrents 详情页 |
| 多语规则页 | 7 | PsyCat 英/德/西/法/意/葡/波兰语页面 |
| 菲律宾 | 2 | Filipino party games、Pinoy parlor games 页面 |
| 印度 | 2 | 成人派对游戏、kitty party 游戏页面 |

逐项字节数和运行限制见 [结构化运行记录](./batch_probe_run_2026-07-16.json)。来源授权、体量、风险和启停理由见 [批量来源审计](../batch_sources.md)。

## 明确保留禁用

- 2.36 GB 的 Namuwiki parquet 及约 9.70 GB 解码内存需求；
- 各语维基标题/文章 dump；
- 70.38 GiB 的单月 Reddit torrent、3.80 TiB 月度全集和 3.97 TiB subreddit 全集；
- WARC、torrent payload 和任何未经范围确认的大语料。

这些入口已经在 manifest 中可复查，但不会因为“尽可能全”就自动下载。下一步应按明确语区和关键词做选择性抽取，而不是把数 TB 原始内容无差别落盘。
