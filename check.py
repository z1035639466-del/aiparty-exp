"""只读检查 outputs/ 中的游戏 JSON；本脚本不会修改任何待检查文件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
OUTPUTS_DIR = ROOT / "outputs"
WHITELIST_PATH = ROOT / "whitelist.json"
REQUIRED_FIELDS = (
    "game_title",
    "players",
    "props_dealt",
    "flow",
    "rules",
    "reveal_text",
    "reveal_signature",
)
SKILL_CARD_ATOMS = {"免死", "反弹", "加倍", "透视", "点名", "跳过", "交换"}
EFFECT_FIELD_NAMES = {"effect", "card_effect", "cardeffect", "效果", "技能效果", "技能卡效果"}


def load_whitelist() -> dict[str, set[str]]:
    """读取白名单；只读取配置文件，不写入任何文件。"""
    with WHITELIST_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return {key: set(data[key]) for key in ("props", "mechanics", "visibility")}


def is_nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def check_document(data: Any, whitelist: dict[str, set[str]]) -> list[str]:
    """返回所有检查失败原因；不改变 data。"""
    if not isinstance(data, dict):
        return ["根节点必须是 JSON 对象"]

    errors: list[str] = []
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        errors.append(f"缺少必填字段: {', '.join(missing)}")

    rules = data.get("rules")
    if not isinstance(rules, list):
        errors.append("rules 必须是数组")
    else:
        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                errors.append(f"rules[{index}] 必须是对象")
                continue
            mechanic = rule.get("mechanic")
            if mechanic not in whitelist["mechanics"]:
                errors.append(f"rules[{index}].mechanic 不在机制白名单: {mechanic!r}")
            if not is_nonempty(rule.get("plain_rule")):
                errors.append(f"rules[{index}].plain_rule 为空或缺失")
            visibility = rule.get("visibility")
            if visibility not in whitelist["visibility"]:
                errors.append(f"rules[{index}].visibility 不合法: {visibility!r}")

    props_dealt = data.get("props_dealt")
    if not isinstance(props_dealt, list):
        errors.append("props_dealt 必须是数组")
    else:
        for index, prop_dealt in enumerate(props_dealt, start=1):
            if not isinstance(prop_dealt, dict):
                errors.append(f"props_dealt[{index}] 必须是对象")
                continue
            prop = prop_dealt.get("prop")
            if prop not in whitelist["props"]:
                errors.append(f"props_dealt[{index}].prop 不在道具白名单: {prop!r}")
            visibility = prop_dealt.get("visibility")
            if visibility not in whitelist["visibility"]:
                errors.append(f"props_dealt[{index}].visibility 不合法: {visibility!r}")

    atoms = sorted(skill_card_effects(data))
    if atoms:
        errors.append(f"禁止使用技能卡效果原子: {', '.join(atoms)}")
    return errors


def check_file(path: Path, whitelist: dict[str, set[str]]) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"JSON 不可解析: {error}"]
    return check_document(data, whitelist)


def main() -> int:
    try:
        whitelist = load_whitelist()
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"挂 whitelist.json: 无法读取白名单: {error}")
        return 1

    files = sorted(OUTPUTS_DIR.glob("*.json")) if OUTPUTS_DIR.is_dir() else []
    for path in files:
        errors = check_file(path, whitelist)
        if errors:
            print(f"挂 {path.name}: {'；'.join(errors)}")
        else:
            print(f"过 {path.name}: 检查通过")
    return 1 if any(check_file(path, whitelist) for path in files) else 0


if __name__ == "__main__":
    raise SystemExit(main())
