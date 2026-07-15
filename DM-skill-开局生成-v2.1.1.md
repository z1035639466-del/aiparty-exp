⚠ 指针存根，非正典正文——严禁作为系统提示投喂
# DM Skill · 开局生成（aiparty）· v2.1.1 —— 指针存根（非正典）

> **本文件不是正典，请勿在此编辑或从此引用。**
>
> 开局生成规范的**唯一正典**为：
>
> **`docs/specs/DM-skill-v2.1.1.md`**
>
> 正典完整性由同目录边车 `docs/specs/DM-skill-v2.1.1.md.sha256` 守护
> （内容 sha256 = `90d7d323d8ec3fe53b1fa35688ce842a54a1cdf15ee7e82ec6d43a35e2b5dd13`，335 行，LF）；
> 校验：`cd docs/specs && sha256sum -c DM-skill-v2.1.1.md.sha256`。
>
> 生成链路（`run_ds.py` 的 `SPEC_RELPATH`）自 v2.1.1 起只读该正典路径；本根目录文件是指针存根，
> 仅为保持 `DM-skill-开局生成-vX.Y.md` 历史命名可发现性而保留。
> v1.1–v2.1 的根目录同名文件仍是各自版本的历史留存，不受此变更影响；
> `docs/specs/DM-skill-v2.1.md`、`docs/specs/DM-skill-v2.0.md` 亦原位保留为历史仪器（卷宗引用它们）。
>
> v2.1.1 = r1 归因三查修正案（教材文件相对 v2.1 属独立正典件）+ 一处修闸：
> check.py / AiParty designValidator.ts 的 `on_timeout.effect=scoring` scoring_ref 落点
> 与其余四落点同式收非空数组（scoring_ref 一律非空数组，单值 `["x"]`）。
> 教材 CI（`tools/textbook_ci.py`）15/15 全绿方入库。
>
> 版本进程登记单点在 AiParty 正典源 `docs/specs/README.md`；exp 不设第二登记簿。
>
> 配套设计层 schema、原语库正典见（v2.1.1 未改字段键，仍冻结于 v2.0）：
> - `docs/specs/design-layer-v2.0.md`
> - `docs/specs/spec-prop-library-v0-final.md`
