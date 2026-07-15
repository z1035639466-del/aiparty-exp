"""检查 outputs/ 中的游戏 JSON；本脚本永不修改任何待检查件（被检件保持纯设计层）。

check.py v2.1——分层闸：
  · 硬闸 = 机械可判、误杀率零，拒件（沿用现有错误结构）。
  · 软闸 = 启发式 warning，不拒件、不影响 pass 判定，改写入旁车文件 <件名>.warnings.json 供裁判读取。
权威源：docs/specs/design-layer-v2.0.md §1–§4 + spec-prop-library-v0-final.md（C5/C6/C7 正典回写）。
白名单一律不变：mechanic 15 串 / visibility 3 原子 / props 14 件 / effect 形态 +N|-N|惩罚(轻|中|重)。

v2.1 两条增改（正典同规格，AiParty validator 侧同步移植）：
  ① expr 可解析硬闸：判定 source=expr 的 expr 须为可解析表达式且引用 state 键，散文自由文本拒（活证 dsT_A_v20_01）。
  ② dead_prop 口径修正：读道具正典表「引用类型」列，免引用道具（沙漏/记分板/匿名投票器/公共看板）不记 dead_prop；
     check 读表不硬编码。软闸输出改旁车文件 <件名>.warnings.json，被检件保持纯设计层。
"""

from __future__ import annotations

import ast
import json
import re
from collections import namedtuple
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = ROOT / "outputs"
WHITELIST_PATH = ROOT / "whitelist.json"
EXCLUSIONS_PATH = ROOT / "check_exclusions.json"

# 本校验器实现的规范版本——供运行链路版本直接核验。
SPEC_VERSION = "v2.1"
SPEC_SOURCES = (
    "docs/specs/DM-skill-v2.0.md",
    "docs/specs/design-layer-v2.0.md",
    "docs/specs/spec-prop-library-v0-final.md",
)

REQUIRED_FIELDS = (
    "game_title",
    "players",
    "props_dealt",
    "flow",
    "rules",
    "settlement",  # v1.9 顶层 settlement 三键必填的载体
    "reveal_text",
    "reveal_signature",
)
SKILL_CARD_ATOMS = {"免死", "反弹", "加倍", "透视", "点名", "跳过", "交换"}
EFFECT_FIELD_NAMES = {"effect", "card_effect", "cardeffect", "效果", "技能效果", "技能卡效果"}

# 六原语（C7 账本封闭的归约目标）
SIX_PRIMITIVES = {"show", "ask", "random", "timer", "state", "fx"}
# 内容填装点 / 引擎派生点前缀：放行、不做值校验
GEN_PREFIXES = ("$gen.", "$派生:")
PENALTY_MECHANICS = {"惩罚(轻)", "惩罚(中)", "惩罚(重)"}
# effect 数值形态：+N / -N（惩罚(档) 不计作数值增减来源）
DELTA_RE = re.compile(r"^[+-]\d+$")
# 道具引用形态（C5）：prop:<名> / prop_reveal:<名>
PROP_REF_RE = re.compile(r"^(prop|prop_reveal):(.+)$")
# 现实物品命名空间前缀（C7 机器槽隔离硬闸）
REAL_PROP_PREFIXES = ("real:", "real_prop:", "props_required:", "现实物品:")
# 机器槽位（C7 命名空间文法闸 / C5 道具引用落点）
MACHINE_SLOT_KEYS = {
    "verify_source",
    "draw_from",
    "reveal_of",
    "source",
    "selector",
    "trigger",
    "content_from",
}
# win 触发有源检查的分数关键词（C3）
WIN_SCORE_KEYWORDS = ("分", "总分", "最高", "最低", "得分", "名次", "积分")
# C4 散文↔参数数字一致性抽查涉及的参数键
PROSE_NUMBER_KEYS = ("seconds", "timeout_s", "cap", "delta", "challenge_window_s")

# —— v2.1 ① 判定 source=expr 的 expr 硬闸 ——
# state:<键> / prop:<名> / prop_reveal:<名> 机器槽引用形态（用于折成占位标识符后解析）
EXPR_STATE_REF_RE = re.compile(r"state:[^\s()><=!&|+\-*/%,]+")
EXPR_PROP_REF_RE = re.compile(r"(?:prop|prop_reveal):[^\s()><=!&|+\-*/%,]+")
# 可解析判定表达式只许比较/布尔/算术/一元/常量/标识符结构——散文自由文本会解析失败或含非法结点
_ALLOWED_EXPR_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv, ast.Pow,
    ast.USub, ast.UAdd,
)

# —— v2.1 ② 软闸旁车文件 ——
WARNINGS_SIDECAR_SUFFIX = ".warnings.json"

CheckResult = namedtuple("CheckResult", ["errors", "warnings"])


# —— 分机制 params 契约（allowed 键集就此冻结 / required 取自 C1 表 / fixed 为定值键） ——
# scoring_ref_at: top=顶层必填；none=无；其余为专用嵌套位（见 _check_scoring_ref_presence）
MECHANIC_SPEC: dict[str, dict[str, Any]] = {
    "同时提交": {
        "allowed": {"prompt", "input_kind", "options", "timeout_s", "reveal", "scoring_ref"},
        "required": ["prompt", "input_kind", "timeout_s", "reveal"],
        "fixed": {"reveal": "simultaneous"},
        "enum": {"input_kind": {"options", "free_text"}},
        "scoring_ref_at": "top",
    },
    "限时": {
        "allowed": {"seconds", "visible_countdown", "on_timeout"},
        "required": ["seconds", "on_timeout"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "limit",
    },
    "判定": {
        "allowed": {"source", "question", "verdict_options", "expr", "ai_overridable", "on"},
        "required": ["source", "question", "on"],
        "fixed": {},
        "enum": {"source": {"consensus", "expr", "ai"}},
        "scoring_ref_at": "verdict",
    },
    "加减分": {
        "allowed": {"who", "delta", "scoring_ref"},
        "required": ["who", "delta"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "top",
    },
    "声明质疑": {
        "allowed": {
            "claim_prompt", "challengers", "challenge_window_s", "verify_source",
            "verify_reveals", "identity_resolution", "raising", "raise_cap",
            "on_cap", "on_liar", "on_false_accuse",
        },
        "required": [
            "claim_prompt", "challengers", "challenge_window_s",
            "verify_source", "verify_reveals", "on_liar", "on_false_accuse",
        ],
        "fixed": {},
        "enum": {"verify_reveals": {"prop_state", "identity"}},
        "scoring_ref_at": "claim",
    },
    "续押喊停": {
        "allowed": {"draw_from", "continue_prompt", "bust_when", "cap", "on_cap", "bank_on_stop", "scoring_ref"},
        "required": ["draw_from", "continue_prompt", "bust_when", "cap", "on_cap", "bank_on_stop"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "top",
    },
    "点名目标": {
        "allowed": {"selector", "target_pool", "on_named"},
        "required": ["selector", "target_pool", "on_named"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "named",
    },
    "转移": {
        "allowed": {"what", "from", "to", "scoring_ref"},
        "required": ["what", "from", "to"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "top",
    },
    "揭示": {
        "allowed": {"reveal_of", "to", "once", "identity_resolution"},
        "required": ["reveal_of", "to", "once"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "none",
    },
    "回合推进": {
        "allowed": {"order"},
        "required": ["order"],
        "fixed": {"order": "round_robin"},
        "enum": {},
        "scoring_ref_at": "none",
    },
    "受限沟通": {
        "allowed": {"channel", "enforce"},
        "required": ["channel", "enforce"],
        "fixed": {"enforce": "channel_only"},
        "enum": {},
        "scoring_ref_at": "none",
    },
    "传递链": {
        "allowed": {"content_from", "order", "each_sees", "replay"},
        "required": ["content_from", "order", "each_sees", "replay"],
        "fixed": {"order": "seat", "each_sees": "prev_only"},
        "enum": {},
        "scoring_ref_at": "none",
    },
}
# 惩罚三档共块（scoring_ref 指向的 event effect 必须同档，见 _check_penalty_tier）
for _tier in PENALTY_MECHANICS:
    MECHANIC_SPEC[_tier] = {
        "allowed": {"who", "pool", "scoring_ref"},
        "required": ["who", "pool"],
        "fixed": {},
        "enum": {},
        "scoring_ref_at": "top",
    }


def load_whitelist() -> dict[str, set[str]]:
    """读取白名单；只读取配置文件，不写入任何文件。

    v2.1 ②：另从道具正典表「引用类型」列（prop_reference_types）派生免引用道具集
    （沙漏/记分板/匿名投票器/公共看板等——读表不硬编码），供 dead_prop 软闸豁免。
    """
    with WHITELIST_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    result: dict[str, set[str]] = {key: set(data[key]) for key in ("props", "mechanics", "visibility")}
    ref_types = data.get("prop_reference_types", {})
    result["reference_exempt"] = {
        name for name, kind in ref_types.items() if kind == "免引用"
    } if isinstance(ref_types, dict) else set()
    return result


def whitelist_declared_version() -> str | None:
    """读取白名单自声明的 schema_version（缺失返回 None），用于运行链路版本对账。"""
    try:
        with WHITELIST_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("schema_version")
    return version if isinstance(version, str) else None


def load_exclusions(path: Path = EXCLUSIONS_PATH) -> dict[str, str]:
    """读取冻结评测集排除清单；键为文件名，值为保留原因。"""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not all(
        isinstance(name, str) and isinstance(reason, str) and reason.strip()
        for name, reason in data.items()
    ):
        raise TypeError("排除清单必须是 {文件名: 非空原因} 对象")
    return data


def is_nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_gen(value: Any) -> bool:
    """内容填装点 / 引擎派生点前缀串：放行、不做值校验。"""
    return isinstance(value, str) and value.startswith(GEN_PREFIXES)


def values(value: Any) -> Iterable[Any]:
    """深度遍历 JSON 值，用于发现独立的技能卡原子。"""
    if isinstance(value, dict):
        for item in value.values():
            yield from values(item)
    elif isinstance(value, list):
        for item in value:
            yield from values(item)
    else:
        yield value


def iter_strings(value: Any) -> Iterable[str]:
    """深度遍历，产出全部字符串叶子。"""
    for leaf in values(value):
        if isinstance(leaf, str):
            yield leaf


def skill_card_effects(value: Any) -> set[str]:
    """返回被作为效果字段使用或单独出现的预留技能卡原子。"""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in EFFECT_FIELD_NAMES:
                for candidate in values(child):
                    if isinstance(candidate, str):
                        found.update(atom for atom in SKILL_CARD_ATOMS if atom in candidate)
            found.update(skill_card_effects(child))
    elif isinstance(value, list):
        for child in value:
            found.update(skill_card_effects(child))
    elif isinstance(value, str) and value.strip() in SKILL_CARD_ATOMS:
        found.add(value.strip())
    return found


def collect_scoring_refs(value: Any) -> list[str]:
    """递归收集任意深度下键名为 scoring_ref 的全部字符串（含数组展开）。C2 收集器。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "scoring_ref":
                if isinstance(child, str):
                    found.append(child)
                elif isinstance(child, list):
                    found.extend(item for item in child if isinstance(item, str))
            else:
                found.extend(collect_scoring_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_scoring_refs(child))
    return found


def collect_prop_refs(value: Any) -> list[tuple[str, str]]:
    """递归收集 prop:<名> / prop_reveal:<名> 形态的道具引用（与 scoring_ref 收集器同构）。C5。"""
    found: list[tuple[str, str]] = []
    for leaf in iter_strings(value):
        match = PROP_REF_RE.match(leaf.strip())
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


def collect_machine_slot_values(value: Any) -> list[tuple[str, str]]:
    """递归收集机器槽位（MACHINE_SLOT_KEYS）承载的字符串值，用于 C7 命名空间文法闸。"""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in MACHINE_SLOT_KEYS:
                for leaf in iter_strings(child):
                    found.append((key, leaf))
            found.extend(collect_machine_slot_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_machine_slot_values(child))
    return found


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def as_number_token(value: Any) -> str | None:
    """把参数值折成阿拉伯数字串用于 C4 抽查；$gen/$派生 与非数字返回 None。"""
    if is_gen(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        digits = re.findall(r"\d+", value)
        return digits[0] if len(digits) == 1 else None
    return None


def expr_gate_reason(expr: Any) -> str | None:
    """v2.1 ①：判定 source=expr 的 expr 硬闸。返回 None=合法；否则返回错因短语。

    expr 须为可解析表达式且引用 state 键：散文自由文本（无 state 引用 / 解析失败 / 含非法结点）一律拒。
    `$gen.`/`$派生:` 填装点放行（与全脚本口径一致，交由内容层填实后再校验）。
    """
    if is_gen(expr):
        return None
    if not is_nonempty(expr):
        return "缺失或为空"
    if not EXPR_STATE_REF_RE.search(expr):
        return "未引用任何 state 键（须含 state:<键>，散文自由文本拒）"
    # 把 state:/prop: 引用折成占位标识符，其余结构须能解析为一个表达式
    normalized = EXPR_STATE_REF_RE.sub("_s", expr)
    normalized = EXPR_PROP_REF_RE.sub("_p", normalized)
    try:
        tree = ast.parse(normalized.strip(), mode="eval")
    except SyntaxError:
        return "非可解析表达式（疑似散文自由文本）"
    if not all(isinstance(node, _ALLOWED_EXPR_NODES) for node in ast.walk(tree)):
        return "含非法表达式结构（仅许比较/布尔/算术/常量/state 引用）"
    return None


# ----------------------------------------------------------------------------
# §1 params 结构校验（硬闸）
# ----------------------------------------------------------------------------
def _require_nested(params: dict, path: list[str], label: str, errors: list[str], must_ref: bool) -> None:
    """沿嵌套路径校验存在且（must_ref 时）scoring_ref 非空。"""
    node: Any = params
    for key in path:
        if not isinstance(node, dict) or key not in node:
            errors.append(f"{label} 缺失（需 {'.'.join(path)}）")
            return
        node = node[key]
    if must_ref:
        if not isinstance(node, dict):
            errors.append(f"{label} 必须是对象且含 scoring_ref")
            return
        ref = node.get("scoring_ref")
        if not (is_nonempty(ref) or (isinstance(ref, list) and ref)):
            errors.append(f"{label} 的 scoring_ref 缺失或为空")


def _check_scoring_ref_presence(mechanic: str, params: dict, index: int, errors: list[str]) -> None:
    where = MECHANIC_SPEC[mechanic]["scoring_ref_at"]
    tag = f"rules[{index}].params"
    if where == "none":
        return
    if where == "top":
        ref = params.get("scoring_ref")
        if not (is_nonempty(ref) or (isinstance(ref, list) and ref)):
            errors.append(f"{tag}.scoring_ref 缺失或为空（{mechanic} 必填顶层 scoring_ref）")
    elif where == "limit":
        on_timeout = params.get("on_timeout")
        if isinstance(on_timeout, dict):
            effect = on_timeout.get("effect")
            if effect not in {"goto", "scoring"}:
                errors.append(f"{tag}.on_timeout.effect 必须为 goto|scoring，实为 {effect!r}")
            elif effect == "goto" and not is_nonempty(on_timeout.get("goto")):
                errors.append(f"{tag}.on_timeout.goto 缺失（effect=goto 时必填）")
            elif effect == "scoring" and not is_nonempty(on_timeout.get("scoring_ref")):
                errors.append(f"{tag}.on_timeout.scoring_ref 缺失（effect=scoring 时必填）")
    elif where == "verdict":
        _require_nested(params, ["on", "过"], f"{tag}.on.过", errors, must_ref=True)
        _require_nested(params, ["on", "不过"], f"{tag}.on.不过", errors, must_ref=True)
    elif where == "claim":
        _require_nested(params, ["on_liar"], f"{tag}.on_liar", errors, must_ref=True)
        _require_nested(params, ["on_false_accuse"], f"{tag}.on_false_accuse", errors, must_ref=True)
    elif where == "named":
        _require_nested(params, ["on_named"], f"{tag}.on_named", errors, must_ref=True)


def _check_conditional_required(mechanic: str, params: dict, index: int, errors: list[str]) -> None:
    tag = f"rules[{index}].params"
    if mechanic == "同时提交":
        if params.get("input_kind") == "options" and not params.get("options"):
            errors.append(f"{tag}.options 缺失（input_kind=options 时必填）")
    elif mechanic == "判定":
        source = params.get("source")
        if source == "consensus" and not params.get("verdict_options"):
            errors.append(f"{tag}.verdict_options 缺失（source=consensus 时必填）")
        elif source == "expr":
            reason = expr_gate_reason(params.get("expr"))
            if reason is not None:
                errors.append(f"{tag}.expr {reason}（source=expr 须为可解析表达式且引用 state 键）")
        elif source == "ai" and params.get("ai_overridable") is not True:
            errors.append(f"{tag}.ai_overridable 必须为 true（source=ai 时人类共识永远最高）")
    elif mechanic == "声明质疑":
        # v1.7 身份终局化 —— 见 §3 不变量
        if params.get("verify_reveals") == "identity":
            if params.get("identity_resolution") not in {"terminal", "reshuffle"}:
                errors.append(
                    f"{tag}.identity_resolution 必填 terminal|reshuffle"
                    f"（verify_reveals=identity，v1.7 身份终局化）"
                )


def validate_params(mechanic: str, rule: dict, index: int) -> list[str]:
    """单条 rule 的 params 结构硬闸校验（oneOf 按 mechanic 判别）。"""
    errors: list[str] = []
    tag = f"rules[{index}].params"
    params = rule.get("params")
    if not isinstance(params, dict):
        errors.append(f"{tag} 缺失或非对象（v2.0 每条 rule 必带结构化 params 块）")
        return errors

    spec = MECHANIC_SPEC[mechanic]
    # 未知键 → 硬闸（键集冻结于 v2.0，防散文回渗）
    unknown = sorted(set(params) - spec["allowed"])
    if unknown:
        errors.append(f"{tag} 含未知参数键（键集已冻结）: {', '.join(unknown)}")
    # 必填键
    for key in spec["required"]:
        value = params.get(key)
        present = key in params and value is not None and value != "" and value != []
        if not present:
            errors.append(f"{tag}.{key} 缺失（{mechanic} 必填键）")
    # 定值键
    for key, fixed in spec["fixed"].items():
        if key in params and params[key] != fixed:
            errors.append(f"{tag}.{key} 必须为 {fixed!r}，实为 {params[key]!r}")
    # 闭合枚举键
    for key, choices in spec["enum"].items():
        if key in params and params[key] not in choices and not is_gen(params[key]):
            errors.append(f"{tag}.{key} 取值非法 {params[key]!r}（须 ∈ {sorted(choices)}）")
    # delta 形态：+N|-N
    if mechanic == "加减分" and "delta" in params:
        delta = params["delta"]
        if not is_gen(delta) and not (isinstance(delta, str) and DELTA_RE.match(delta)):
            errors.append(f"{tag}.delta 形态非法 {delta!r}（须为 +N|-N）")
    # scoring_ref 落点
    _check_scoring_ref_presence(mechanic, params, index, errors)
    # 条件必填
    _check_conditional_required(mechanic, params, index, errors)
    # A_v18 递增不变量（续押喊停）——§3
    if mechanic == "续押喊停":
        cap = params.get("cap")
        if not (isinstance(cap, int) and not isinstance(cap, bool) and cap > 0) and not is_gen(cap):
            errors.append(f"{tag}.cap 须为正整数（A_v18 递增序列上限）")
        if params.get("on_cap") != "force_settle":
            errors.append(f"{tag}.on_cap 须为 force_settle（A_v18 到顶强制结算）")
    return errors


# ----------------------------------------------------------------------------
# 主体检查
# ----------------------------------------------------------------------------
def check_document(data: Any, whitelist: dict[str, set[str]]) -> CheckResult:
    """返回硬闸 errors + 软闸 warnings；不改变 data。"""
    if not isinstance(data, dict):
        return CheckResult(["根节点必须是 JSON 对象"], [])

    errors: list[str] = []
    warnings: list[str] = []

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        errors.append(f"缺少必填字段: {', '.join(missing)}")

    props_lib = whitelist.get("props", set())
    dealt_props: set[str] = set()

    # —— props_dealt（数字道具，14 库白名单） ——
    props_dealt = data.get("props_dealt")
    if props_dealt is not None and not isinstance(props_dealt, list):
        errors.append("props_dealt 必须是数组")
    elif isinstance(props_dealt, list):
        for index, prop_dealt in enumerate(props_dealt, start=1):
            if not isinstance(prop_dealt, dict):
                errors.append(f"props_dealt[{index}] 必须是对象")
                continue
            prop = prop_dealt.get("prop")
            if prop not in props_lib:
                errors.append(f"props_dealt[{index}].prop 不在道具白名单: {prop!r}")
            else:
                dealt_props.add(prop)
            visibility = prop_dealt.get("visibility")
            if visibility not in whitelist.get("visibility", set()):
                errors.append(f"props_dealt[{index}].visibility 不合法: {visibility!r}")

    # —— props_required（C6 现实物品，新增顶层字段） ——
    props_required = data.get("props_required")
    real_props: list[str] = []
    if props_required is None:
        props_required = []  # 缺省=纯数字局（空列表），旧「纯数字须为空」条废弃
    if not isinstance(props_required, list) or not all(isinstance(x, str) for x in props_required):
        errors.append("props_required 必须是字符串数组（C6 现实物品清单）")
    else:
        real_props = [x for x in props_required if x.strip()]
        if real_props and not is_nonempty(data.get("safety_note")):
            # 自由词表的代价是安全滤前置：props_required 非空 → safety_note 联动
            errors.append("props_required 非空时 safety_note 必填（C6 安全滤前置/contentFilter 联动）")

    # —— rules[] + params ——
    rules = data.get("rules")
    all_scoring_refs: list[str] = []
    all_prop_refs: list[tuple[str, str]] = []
    if not isinstance(rules, list):
        errors.append("rules 必须是数组")
        rules = []
    else:
        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                errors.append(f"rules[{index}] 必须是对象")
                continue
            mechanic = rule.get("mechanic")
            if mechanic not in whitelist.get("mechanics", set()):
                errors.append(f"rules[{index}].mechanic 不在机制白名单: {mechanic!r}")
            # plain_rule 存在且非空（从"机器读取源"除名，只作人读断言）
            if not is_nonempty(rule.get("plain_rule")):
                errors.append(f"rules[{index}].plain_rule 为空或缺失")
            visibility = rule.get("visibility")
            if visibility not in whitelist.get("visibility", set()):
                errors.append(f"rules[{index}].visibility 不合法: {visibility!r}")
            # persistent 修饰符（§1.1）：存在时校验 trigger 合法
            persistent = rule.get("persistent")
            if isinstance(persistent, dict):
                if persistent.get("trigger") not in {"report_driven", "background"}:
                    errors.append(f"rules[{index}].persistent.trigger 非法（须 report_driven|background）")

            if mechanic in MECHANIC_SPEC:
                errors.extend(validate_params(mechanic, rule, index))
            params = rule.get("params")
            if isinstance(params, dict):
                all_scoring_refs.extend(collect_scoring_refs(params))
                all_prop_refs.extend(collect_prop_refs(params))
                # C4 散文↔参数数字抽查（软闸）
                warnings.extend(_prose_number_warnings(rule, params, index))
                # C7 机器槽现实物品隔离（硬闸）
                errors.extend(_machine_slot_isolation(params, real_props, index))

    # —— settlement 引用级检查（§2/§3/§4） ——
    settlement = data.get("settlement")
    scoring_events: set[str] = set()
    if settlement is not None:
        if not isinstance(settlement, dict):
            errors.append("settlement 必须是对象")
        else:
            scoring = settlement.get("scoring")
            if not isinstance(scoring, list):
                errors.append("settlement.scoring 必须是数组（v1.9 三键必填）")
                scoring = []
            for sindex, entry in enumerate(scoring, start=1):
                if not isinstance(entry, dict):
                    errors.append(f"settlement.scoring[{sindex}] 必须是对象")
                    continue
                event = entry.get("event")
                if is_nonempty(event):
                    scoring_events.add(event)
                else:
                    errors.append(f"settlement.scoring[{sindex}].event 缺失或为空")
            if not is_nonempty(settlement.get("win")):
                errors.append("settlement.win 缺失或为空（v1.9 三键必填）")
            if not is_nonempty(settlement.get("tiebreak")):
                errors.append("settlement.tiebreak 缺失或为空（v1.9 三键必填/平局兜底）")
            # win 有源（C3 强化版，硬闸）
            errors.extend(_win_has_source(settlement, scoring))

    # —— §2 正向对账（硬闸）：每个 scoring_ref 必须命中 settlement.scoring event ——
    for ref in all_scoring_refs:
        if ref not in scoring_events:
            errors.append(f"scoring_ref 无对账目标: {ref!r} 不在 settlement.scoring[].event")

    # —— 惩罚档位一致性（硬闸）：惩罚(X) 的 scoring_ref → event effect 必须同档 ——
    errors.extend(_penalty_tier_consistency(rules, settlement))

    # —— §6 C5 prop_ref 硬闸 + dead_prop 软闸 ——
    referenced_props: set[str] = set()
    for kind, name in all_prop_refs:
        if name not in props_lib:
            errors.append(f"道具引用 {kind}:{name} 不在 14 道具固定库（C5）")
        elif name not in dealt_props:
            errors.append(f"道具引用 {kind}:{name} 未在 props_dealt 实发（空引用，C5）")
        else:
            referenced_props.add(name)
    # v2.1 ②：免引用道具（正典表「引用类型」列=免引用，如沙漏/记分板/匿名投票器/公共看板）
    # 靠机制结构性消费、不经 prop:/prop_reveal: 引用，实发未引用不算死道具，读表豁免、不硬编码。
    reference_exempt = whitelist.get("reference_exempt", set())
    for prop in sorted(dealt_props - referenced_props):
        if prop in reference_exempt:
            continue
        warnings.append(f"dead_prop:{prop}")

    # —— §2 反向死账目（软闸） ——
    for event in sorted(scoring_events - set(all_scoring_refs)):
        warnings.append(f"dead_ledger:{event}")

    # —— §6 C6 dead_real_prop 软闸（现实物品须在人读散文被引用） ——
    prose_blob = _prose_blob(data)
    for item in real_props:
        if item not in prose_blob:
            warnings.append(f"dead_real_prop:{item}")

    # —— 技能卡效果原子禁令（硬闸，保留） ——
    atoms = sorted(skill_card_effects(data))
    if atoms:
        errors.append(f"禁止使用技能卡效果原子: {', '.join(atoms)}")

    return CheckResult(errors, warnings)


def _prose_blob(data: dict) -> str:
    """拼出人读散文（plain_rule / flow / premise / 惩罚 pool / safety_note）供现实物品引用检查。"""
    parts: list[str] = []
    for key in ("premise", "safety_note", "reveal_text"):
        if isinstance(data.get(key), str):
            parts.append(data[key])
    flow = data.get("flow")
    if isinstance(flow, list):
        parts.extend(x for x in flow if isinstance(x, str))
    rules = data.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                if isinstance(rule.get("plain_rule"), str):
                    parts.append(rule["plain_rule"])
                params = rule.get("params")
                if isinstance(params, dict):
                    pool = params.get("pool")
                    if isinstance(pool, str):
                        parts.append(pool)
    return "\n".join(parts)


def _prose_number_warnings(rule: dict, params: dict, index: int) -> list[str]:
    """C4：plain_rule 中的阿拉伯数字与本条 params 数字不一致 → prose_param_mismatch 软闸。"""
    plain = rule.get("plain_rule")
    if not isinstance(plain, str):
        return []
    prose_numbers = numbers_in(plain)
    if not prose_numbers:  # 散文可能用中文数字，无阿拉伯数字则不抽查（降误报）
        return []
    out: list[str] = []
    for key in PROSE_NUMBER_KEYS:
        if key not in params:
            continue
        token = as_number_token(params[key])
        if token is not None and token not in prose_numbers:
            out.append(f"prose_param_mismatch:rules[{index}].{key}={token}")
    return out


def _machine_slot_isolation(params: dict, real_props: list[str], index: int) -> list[str]:
    """C7：现实物品命名空间出现在任何机器槽 = 硬闸。数字道具引用交由 C5，此处只堵现实物品泄漏。"""
    errors: list[str] = []
    real_set = set(real_props)
    for slot, raw in collect_machine_slot_values(params):
        value = raw.strip()
        if value.startswith(REAL_PROP_PREFIXES):
            errors.append(f"rules[{index}].params.{slot} 引用现实物品命名空间 {value!r}（C7 硬闸：给指称不给感知）")
            continue
        # 裸串直接命中 props_required 项 = 把现实物品塞进判源
        bare = value
        match = PROP_REF_RE.match(value)
        if match:
            bare = match.group(2).strip()
        if bare in real_set:
            errors.append(
                f"rules[{index}].params.{slot} 引用现实物品 {bare!r}"
                f"（C7 硬闸：现实物品判定永远走六原语，不进机器槽）"
            )
    return errors


def _win_has_source(settlement: dict, scoring: list) -> list[str]:
    """C3：win 文本含分/总分/最高/最低 → scoring 至少一条 effect 为 ±N 形态。"""
    win = settlement.get("win")
    if not isinstance(win, str) or not any(k in win for k in WIN_SCORE_KEYWORDS):
        return []
    has_delta = any(
        isinstance(entry, dict) and isinstance(entry.get("effect"), str) and DELTA_RE.match(entry["effect"])
        for entry in scoring
    )
    if not has_delta:
        return ["settlement.win 引用分数/名次，但 scoring 无 ±N 数值增减来源（C3；惩罚(档) 不计作来源）"]
    return []


def _penalty_tier_consistency(rules: list, settlement: Any) -> list[str]:
    """§1 末条：惩罚(X) 机制的 scoring_ref 指向的 event，其 effect 必须为 惩罚(X) 同档。"""
    errors: list[str] = []
    if not isinstance(settlement, dict):
        return errors
    scoring = settlement.get("scoring")
    if not isinstance(scoring, list):
        return errors
    effect_by_event: dict[str, Any] = {}
    for entry in scoring:
        if isinstance(entry, dict) and is_nonempty(entry.get("event")):
            effect_by_event[entry["event"]] = entry.get("effect")
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict) or rule.get("mechanic") not in PENALTY_MECHANICS:
            continue
        tier = rule["mechanic"]
        params = rule.get("params")
        if not isinstance(params, dict):
            continue
        for ref in collect_scoring_refs(params):
            if ref in effect_by_event and effect_by_event[ref] != tier:
                errors.append(
                    f"rules[{index}] 惩罚档位不一致：scoring_ref {ref!r} 的 effect "
                    f"{effect_by_event[ref]!r} ≠ 机制档位 {tier}"
                )
    return errors


def check_file(path: Path, whitelist: dict[str, set[str]]) -> CheckResult:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return CheckResult([f"JSON 不可解析: {error}"], [])
    return check_document(data, whitelist)


def warnings_sidecar_path(path: Path) -> Path:
    """被检件 <件名>.json → 旁车软闸件 <件名>.warnings.json（同目录）。"""
    return path.with_name(path.stem + WARNINGS_SIDECAR_SUFFIX)


def write_warnings_sidecar(path: Path, warnings: list[str]) -> Path | None:
    """v2.1 ②：软闸写入旁车文件，被检件保持纯设计层（永不回写件内）。

    有软闸→写/覆盖旁车件并返回其路径；无软闸→清掉可能存在的旧旁车件并返回 None。
    """
    sidecar = warnings_sidecar_path(path)
    if not warnings:
        if sidecar.exists():
            sidecar.unlink()
        return None
    payload = {
        "file": path.name,
        "spec_version": SPEC_VERSION,
        "warnings": list(warnings),
    }
    with sidecar.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return sidecar


def main() -> int:
    try:
        whitelist = load_whitelist()
        exclusions = load_exclusions()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"挂检查配置: 无法读取白名单或排除清单: {error}")
        return 1

    # 运行链路版本自述：校验器实现版本 + 白名单声明版本，供直接核验
    wl_version = whitelist_declared_version()
    print(f"check.py 实现规范: {SPEC_VERSION}（正典 {SPEC_SOURCES[0]}）；白名单声明: {wl_version or '未声明'}")
    if wl_version is not None and wl_version != SPEC_VERSION:
        print(f"⚠ 版本漂移: 白名单 schema_version={wl_version!r} ≠ 校验器 SPEC_VERSION={SPEC_VERSION!r}")

    # 旁车软闸件本身也是 *.json，扫描时先排除，免被当设计层件回检。
    all_files = sorted(
        path for path in OUTPUTS_DIR.glob("*.json")
        if not path.name.endswith(WARNINGS_SIDECAR_SUFFIX)
    ) if OUTPUTS_DIR.is_dir() else []
    files = [path for path in all_files if path.name not in exclusions]
    for path in all_files:
        if path.name in exclusions:
            print(f"略 {path.name}: {exclusions[path.name]}")
    failed = False
    for path in files:
        result = check_file(path, whitelist)
        if result.errors:
            failed = True
            print(f"挂 {path.name}: {'；'.join(result.errors)}")
        else:
            print(f"过 {path.name}: 检查通过")
        # 软闸 warning 改写入旁车文件 <件名>.warnings.json，不回写件内、不影响 pass 判定，供裁判 v0.3 读取
        sidecar = write_warnings_sidecar(path, list(result.warnings))
        if sidecar is not None:
            print(f"  ⚠ 软闸 {path.name}: {len(result.warnings)} 条 → 旁车 {sidecar.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
