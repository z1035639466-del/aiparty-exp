# DeepSeek 批量生成器设计

## 目标

新增 `run_ds.py`，用 `deepseek-v4-flash`、`https://api.deepseek.com`、`temperature=0.8` 执行三批互相独立的生成请求：关闭思考的矩阵批、开启思考且 `reasoning_effort=high` 的矩阵批、关闭思考的 B 多样性批。

## 调用与文件

- system 消息为 `DM-skill-开局生成-v1.8.md` 全文，user 消息为对应 `inputs/input_*.json` 的原始全文。
- 固定 `max_tokens`，不启用 `response_format`，不携带对话历史。
- 输出名分别为 `ds_A_v18_01.json`、`dsT_A_v18_01.json` 和 `ds_B_div_01.json` 形式。
- 非 2xx、网络错误或不可解析 JSON 最多重试 3 次；每次失败原件均保留，重试文件在目标文件 stem 后追加 `_r1`、`_r2`、`_r3`。
- API key 从环境变量或显式 `--key-file` 读取，不写入仓库、输出或日志。

## 预算与日志

- 美元价格按缓存未命中输入 `$0.14/M token`、输出 `$0.28/M token` 计算。
- `--estimate` 在任何请求前打印三批完整预估。估算采用本地保守 prompt token 上界与每发完整 `max_tokens`，思考 token 计入 completion 计费，不重复计价。
- 真实执行必须显式传 `--run`。每发前用同一完整单发上界检查累计真实成本加该上界是否超过 `$5`；不足则不发请求、立即报错停机。
- `usage_log.csv` 每个尝试一行：文件名、model、thinking、reasoning_effort、prompt/completion/reasoning token、延迟、成本、状态。

## 校验与汇总

运行结束后，脚本只校验本次成功生成的 DS 文件，并执行全量 `check.py`。汇总按 v11 与 v18 分组；三份 `fable_*_v11_01.json` 标注“历史产物，不计入矩阵”，既有失败只报告不修改。最后打印总 token、总成本和平均延迟。

