import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


WHITELIST = {
    "props": {
        "身份牌", "骰盅", "密语卡", "盲盒", "虚拟左轮", "定时炸弹", "命运转盘",
        "扑克牌", "骰子", "抽签筒", "匿名投票器", "沙漏", "记分板", "公共看板",
    },
    "mechanics": {
        "点名目标", "转移", "加减分", "惩罚(轻)", "惩罚(中)", "惩罚(重)", "揭示",
        "限时", "回合推进", "声明质疑", "同时提交", "续押喊停", "受限沟通", "判定", "传递链",
    },
    "visibility": {"自己看", "额头", "全场公开"},
}


def valid_doc() -> dict:
    """一份干净通过 v2.0（零硬闸、零软闸）的基线件，供各测试变异。"""
    return {
        "game_title": "测试局",
        "players": ["甲", "乙", "丙"],
        "props_dealt": [
            {"prop": "骰盅", "to": "全体", "visibility": "自己看", "note": "诈唬用"},
        ],
        "props_required": [],
        "flow": ["开局", "结算"],
        "rules": [
            {
                "flavor_name": "同时押注",
                "mechanic": "同时提交",
                "plain_rule": "所有人 15 秒内私密提交一个选择。",
                "visibility": "自己看",
                "params": {
                    "prompt": "选一个",
                    "input_kind": "options",
                    "options": ["A", "B"],
                    "timeout_s": 15,
                    "reveal": "simultaneous",
                    "scoring_ref": "提交结算",
                },
            },
            {
                "flavor_name": "赢家加分",
                "mechanic": "加减分",
                "plain_rule": "赢家加 1 分。",
                "visibility": "全场公开",
                "params": {"who": "winner", "delta": "+1", "scoring_ref": "得分事件"},
            },
            {
                "flavor_name": "续押",
                "mechanic": "续押喊停",
                "plain_rule": "最多 8 轮，到顶强制结算。",
                "visibility": "全场公开",
                "params": {
                    "draw_from": "prop:骰盅",
                    "continue_prompt": "继续 / 收手",
                    "bust_when": "$派生:爆掉条件",
                    "cap": 8,
                    "on_cap": "force_settle",
                    "bank_on_stop": "累积分",
                    "scoring_ref": "续押结算",
                },
            },
            {
                "flavor_name": "输家轻罚",
                "mechanic": "惩罚(轻)",
                "plain_rule": "输家接一次轻罚。",
                "visibility": "全场公开",
                "params": {"who": "loser", "pool": "$gen.penalty_轻", "scoring_ref": "轻罚事件"},
            },
        ],
        "settlement": {
            "scoring": [
                {"event": "提交结算", "who": "all", "effect": "+1"},
                {"event": "得分事件", "who": "winner", "effect": "+1"},
                {"event": "续押结算", "who": "all", "effect": "+2"},
                {"event": "轻罚事件", "who": "loser", "effect": "惩罚(轻)"},
            ],
            "win": "总分最高者胜。",
            "tiebreak": "系统随机裁决。",
        },
        "reveal_text": "揭幕。",
        "reveal_signature": "融合自 甲 的梗。",
        "safety_note": "全程安全。",
    }


def errors_of(doc: dict) -> list[str]:
    return check.check_document(doc, WHITELIST).errors


def warnings_of(doc: dict) -> list[str]:
    return check.check_document(doc, WHITELIST).warnings


class BaselineTests(unittest.TestCase):
    def test_valid_v2_document_passes_clean(self):
        result = check.check_document(valid_doc(), WHITELIST)
        self.assertEqual([], result.errors)
        self.assertEqual([], result.warnings)


class ParamsStructureTests(unittest.TestCase):
    def test_missing_params_block_hard_fails(self):
        doc = valid_doc()
        del doc["rules"][1]["params"]
        self.assertTrue(any("params 缺失或非对象" in e for e in errors_of(doc)))

    def test_unknown_param_key_frozen_rejected(self):
        doc = valid_doc()
        doc["rules"][1]["params"]["散文回渗"] = "x"
        self.assertTrue(any("未知参数键" in e for e in errors_of(doc)))

    def test_missing_required_key_rejected(self):
        doc = valid_doc()
        del doc["rules"][0]["params"]["timeout_s"]
        self.assertTrue(any("timeout_s 缺失" in e for e in errors_of(doc)))

    def test_fixed_value_enforced(self):
        doc = valid_doc()
        doc["rules"][0]["params"]["reveal"] = "sequential"
        self.assertTrue(any("必须为 'simultaneous'" in e for e in errors_of(doc)))

    def test_closed_enum_enforced(self):
        doc = valid_doc()
        doc["rules"][0]["params"]["input_kind"] = "voice"
        self.assertTrue(any("input_kind 取值非法" in e for e in errors_of(doc)))

    def test_conditional_options_required(self):
        doc = valid_doc()
        del doc["rules"][0]["params"]["options"]
        self.assertTrue(any("options 缺失（input_kind=options" in e for e in errors_of(doc)))

    def test_delta_shape_enforced(self):
        doc = valid_doc()
        doc["rules"][1]["params"]["delta"] = "两分"
        self.assertTrue(any("delta 形态非法" in e for e in errors_of(doc)))

    def test_verdict_needs_both_branches(self):
        doc = valid_doc()
        doc["rules"].append({
            "flavor_name": "判", "mechanic": "判定", "plain_rule": "全场共识判过或不过。",
            "visibility": "全场公开",
            "params": {"source": "consensus", "question": "够劲吗", "verdict_options": ["过", "不过"],
                       "on": {"过": {"scoring_ref": "提交结算"}}},
        })
        self.assertTrue(any("on.不过" in e for e in errors_of(doc)))


class InvariantTests(unittest.TestCase):
    def test_av18_on_cap_must_be_force_settle(self):
        doc = valid_doc()
        doc["rules"][2]["params"]["on_cap"] = "loop"
        self.assertTrue(any("on_cap 须为 force_settle" in e for e in errors_of(doc)))

    def test_av18_cap_must_be_positive_int(self):
        doc = valid_doc()
        doc["rules"][2]["params"]["cap"] = 0
        self.assertTrue(any("cap 须为正整数" in e for e in errors_of(doc)))

    def test_v17_identity_resolution_required(self):
        doc = valid_doc()
        doc["rules"].append({
            "flavor_name": "验身份", "mechanic": "声明质疑",
            "plain_rule": "公开声明，任何人可在 10 秒内质疑验证身份。", "visibility": "全场公开",
            "params": {
                "claim_prompt": "我不是内鬼", "challengers": "others", "challenge_window_s": 10,
                "verify_source": "prop_reveal:身份牌", "verify_reveals": "identity",
                "on_liar": {"scoring_ref": "得分事件"}, "on_false_accuse": {"scoring_ref": "得分事件"},
            },
        })
        doc["props_dealt"].append({"prop": "身份牌", "to": "全体", "visibility": "自己看", "note": "x"})
        self.assertTrue(any("identity_resolution 必填" in e for e in errors_of(doc)))

    def test_c7_real_prop_in_machine_slot_hard_fails(self):
        doc = valid_doc()
        doc["props_required"] = ["半瓶白酒"]
        doc["rules"][2]["params"]["draw_from"] = "半瓶白酒"  # 现实物品塞进机器槽
        self.assertTrue(any("C7 硬闸" in e for e in errors_of(doc)))

    def test_c7_real_prop_namespace_prefix_hard_fails(self):
        doc = valid_doc()
        doc["props_required"] = ["瓶子"]
        doc["rules"][2]["params"]["draw_from"] = "real_prop:瓶子"
        self.assertTrue(any("命名空间" in e for e in errors_of(doc)))


class PropTrackTests(unittest.TestCase):
    def test_c5_prop_ref_not_in_library(self):
        doc = valid_doc()
        doc["rules"][2]["params"]["draw_from"] = "prop:魔法棒"
        self.assertTrue(any("不在 14 道具固定库" in e for e in errors_of(doc)))

    def test_c5_prop_ref_not_dealt(self):
        doc = valid_doc()
        doc["rules"][2]["params"]["draw_from"] = "prop:虚拟左轮"  # 合法库但本局未发
        self.assertTrue(any("未在 props_dealt 实发" in e for e in errors_of(doc)))

    def test_dead_prop_soft_warning(self):
        doc = valid_doc()
        doc["props_dealt"].append({"prop": "沙漏", "to": "公共区", "visibility": "全场公开", "note": "x"})
        self.assertIn("dead_prop:沙漏", warnings_of(doc))
        self.assertEqual([], errors_of(doc))  # 软闸不拒件

    def test_props_required_type_hard_fails(self):
        doc = valid_doc()
        doc["props_required"] = [{"名": "瓶子"}]
        self.assertTrue(any("props_required 必须是字符串数组" in e for e in errors_of(doc)))

    def test_props_required_needs_safety_note(self):
        doc = valid_doc()
        doc["props_required"] = ["筷子", "花生"]
        doc["flow"].append("用筷子夹花生")  # 让它们在散文中被引用，避免 dead_real_prop
        doc["safety_note"] = ""
        self.assertTrue(any("safety_note 必填" in e for e in errors_of(doc)))

    def test_dead_real_prop_soft_warning(self):
        doc = valid_doc()
        doc["props_required"] = ["没人提到的道具"]
        self.assertIn("dead_real_prop:没人提到的道具", warnings_of(doc))


class SettlementTests(unittest.TestCase):
    def test_scoring_ref_must_reconcile(self):
        doc = valid_doc()
        doc["rules"][1]["params"]["scoring_ref"] = "查无此账"
        self.assertTrue(any("无对账目标" in e for e in errors_of(doc)))

    def test_dead_ledger_soft_warning(self):
        doc = valid_doc()
        doc["settlement"]["scoring"].append({"event": "无人触达", "who": "all", "effect": "+9"})
        self.assertIn("dead_ledger:无人触达", warnings_of(doc))
        self.assertEqual([], errors_of(doc))

    def test_win_needs_numeric_source(self):
        doc = valid_doc()
        doc["settlement"]["scoring"] = [{"event": "轻罚事件", "who": "loser", "effect": "惩罚(轻)"}]
        # 移除引用不存在 event 的规则，只留惩罚规则，避免其它硬闸掩盖 win 检查
        doc["rules"] = [doc["rules"][3]]
        self.assertTrue(any("无 ±N 数值增减来源" in e for e in errors_of(doc)))

    def test_penalty_tier_consistency(self):
        doc = valid_doc()
        for entry in doc["settlement"]["scoring"]:
            if entry["event"] == "轻罚事件":
                entry["effect"] = "惩罚(中)"  # 与机制 惩罚(轻) 不同档
        self.assertTrue(any("惩罚档位不一致" in e for e in errors_of(doc)))

    def test_settlement_three_keys_required(self):
        doc = valid_doc()
        del doc["settlement"]["tiebreak"]
        self.assertTrue(any("tiebreak 缺失" in e for e in errors_of(doc)))


class ProseTests(unittest.TestCase):
    def test_prose_param_mismatch_soft_warning(self):
        doc = valid_doc()
        doc["rules"][2]["plain_rule"] = "最多 5 轮，到顶强制结算。"  # 散文 5 ≠ params cap 8
        self.assertIn("prose_param_mismatch:rules[3].cap=8", warnings_of(doc))
        self.assertEqual([], errors_of(doc))


class LegacyV18Tests(unittest.TestCase):
    def test_v18_shaped_doc_hard_rejected(self):
        """v1.8 老件（无 params、无 settlement）应被硬闸拒——冒烟只证明闸活着。"""
        doc = valid_doc()
        for rule in doc["rules"]:
            rule.pop("params", None)
        del doc["settlement"]
        errors = errors_of(doc)
        self.assertTrue(errors)
        self.assertTrue(any("settlement" in e for e in errors))
        self.assertTrue(any("params 缺失" in e for e in errors))


class ExclusionManifestTests(unittest.TestCase):
    def test_load_exclusions_returns_filename_reason_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"old.json": "历史产物"}), encoding="utf-8")
            self.assertEqual({"old.json": "历史产物"}, check.load_exclusions(path))

    def test_main_skips_manifest_entries_and_checks_current_files(self):
        with tempfile.TemporaryDirectory() as directory:
            outputs = Path(directory)
            (outputs / "old.json").write_text("not-json", encoding="utf-8")
            (outputs / "current.json").write_text("{}", encoding="utf-8")
            with (
                patch.object(check, "OUTPUTS_DIR", outputs),
                patch.object(check, "load_whitelist", return_value=WHITELIST),
                patch.object(check, "load_exclusions", return_value={"old.json": "历史产物"}),
                patch.object(check, "check_file", return_value=check.CheckResult([], [])) as check_file,
            ):
                self.assertEqual(0, check.main())
            check_file.assert_called_once()
            self.assertEqual("current.json", check_file.call_args.args[0].name)


if __name__ == "__main__":
    unittest.main()
