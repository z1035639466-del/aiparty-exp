import json
import tempfile
import unittest
from pathlib import Path

import build_v20_report as report


def write_json(path: Path, **extra):
    doc = {"valid": True, "warnings": [], "flow": ["同时选择", "答案一致则得分"], "rules": [{"mechanic": "同时提交"}]}
    doc.update(extra)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def write_review(candidate: Path, verdict="过", **fields):
    values = {"死规则": "0", "坍缩": "0", "真空": "0", "伪猜": "0", "占优": "0", "终局": "过", "结算": "过", "落点": "过", "膨胀": "原2条→有效1", "融合": "过", "小局身份": "不适用", "shape": "其他"}
    values.update(fields)
    header = "VERDICT: " + verdict + " | " + " ".join(f"{k}:{v}" for k, v in values.items())
    candidate.with_name(candidate.name + ".review3.md").write_text(header + "\n\n8. 原语落点：过。\n\n10. 小局身份硬否决：不适用。\n", encoding="utf-8")


class BuildReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "outputs").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "retry_manifest_v20.json").write_text(json.dumps({"version": "test", "jobs": []}), encoding="utf-8")
        (self.root / "usage_log.csv").write_text("filename,model,thinking,reasoning_effort,prompt_token,completion_token,reasoning_token,latency_seconds,cost_usd,status\n", encoding="utf-8")
        (self.root / "ds_B_div_shapes.md").write_text("件名 | shape:社推/其他 | 一句实际核心循环 | 机制家族\\nds_B_div_01 | shape:社推 | 限时盘问后投票放逐卧底 | 卧底放逐\\n", encoding="utf-8")
        (self.root / "docs" / "36正典.md").write_text("| 1 | 身份心理战/隐藏角色社交推理 | x |\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def checker(path):
        if not path.exists():
            return False, []
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False, []
        return doc.get("valid", True), doc.get("warnings", [])

    def test_parse_review_multilabel_and_inflation(self):
        candidate = self.root / "outputs" / "sonnet_A_v20_01.json"
        write_json(candidate)
        write_review(candidate, "破", 真空="1", 结算="破", 落点="破")
        parsed = report.parse_review(candidate.with_name(candidate.name + ".review3.md"))
        self.assertEqual("破", parsed["verdict"])
        self.assertEqual("1", parsed["fields"]["真空"])
        self.assertEqual({"original": 2, "effective": 1}, parsed["inflation"])

    def test_pass_at_two_separates_retry_kinds_and_pending(self):
        a = self.root / "outputs" / "sonnet_A_v20_01.json"
        b = self.root / "outputs" / "sonnet_B_v20_01.json"
        c = self.root / "outputs" / "sonnet_C_v20_01.json"
        write_json(a); write_review(a, "过")
        write_json(b); write_review(b, "破")
        write_json(c, valid=False)
        jobs = [
            {"group": "main_verdict", "model": "sonnet", "source": b.name, "start_retry": 1, "max_new_attempts": 1},
            {"group": "main_structure", "model": "sonnet", "source": c.name, "start_retry": 1, "max_new_attempts": 1},
        ]
        (self.root / "retry_manifest_v20.json").write_text(json.dumps({"version": "test", "jobs": jobs}), encoding="utf-8")
        br = self.root / "outputs" / "sonnet_B_v20_01_r1.json"
        cr = self.root / "outputs" / "sonnet_C_v20_01_r1.json"
        write_json(br); write_review(br, "过")
        write_json(cr)  # valid but review is deliberately pending
        data = report.build_data(self.root, self.checker)
        row = next(x for x in data["pass"] if x["model"] == "sonnet")
        self.assertEqual((1, 3), (row["pass_at_1"]["passed"], row["pass_at_1"]["total"]))
        self.assertEqual(1, row["verdict_retry"]["passed"])
        self.assertEqual(0, row["structure_retry"]["passed"])
        self.assertEqual(1, row["pending_retry_reviews"])
        self.assertEqual(2, row["pass_at_2"]["passed"])
        self.assertEqual("valid_pending_review", next(k for k, v in data["status"].items() if k == "valid_pending_review" and v == 1))

    def test_b20_terminal_and_baseline_use_canonical_family(self):
        source = self.root / "outputs" / "ds_B_div_v20_01.json"
        write_json(source, valid=False)
        job = {"group": "b_structure", "model": "ds_B_div", "source": source.name, "start_retry": 1, "max_new_attempts": 2}
        (self.root / "retry_manifest_v20.json").write_text(json.dumps({"version": "test", "jobs": [job]}), encoding="utf-8")
        retry = self.root / "outputs" / "ds_B_div_v20_01_r1.json"
        write_json(retry, flow=["限时盘问", "匿名投票放逐卧底"])
        write_review(retry, "过", shape="社推")
        data = report.build_data(self.root, self.checker)
        self.assertEqual(retry.name, data["b20"][0]["final"])
        self.assertEqual("身份心理战/隐藏角色社交推理", data["b20"][0]["canonical_family"])
        self.assertEqual("身份心理战/隐藏角色社交推理", data["baseline_normalized"][0]["canonical_family"])
        self.assertEqual(1, data["canonical_catalog"]["family_count"])
        self.assertEqual(19, len(data["b20_pending"]))

    def test_usage_and_warning_density(self):
        candidate = self.root / "outputs" / "dsT_A_v20_01.json"
        write_json(candidate, warnings=["w1", "w2"])
        write_review(candidate, "过")
        (self.root / "usage_log.csv").write_text(
            "filename,model,thinking,reasoning_effort,prompt_token,completion_token,reasoning_token,latency_seconds,cost_usd,status\n"
            "dsT_A_v20_01.json,deepseek,true,high,10,20,5,2.5,0.01,success\n", encoding="utf-8")
        data = report.build_data(self.root, self.checker)
        self.assertEqual(2, data["diagnostics"]["dsT"]["warnings"])
        self.assertEqual(2.0, data["diagnostics"]["dsT"]["warnings_per_candidate"])
        self.assertEqual(10, data["usage"]["deepseek"]["prompt_tokens"])

    def test_ds_retry_pass_is_marked_for_sop_followup(self):
        source = self.root / "outputs" / "dsT_A_v20_01.json"
        write_json(source, valid=False)
        job = {"group": "main_structure", "channel": "api", "model": "dsT", "source": source.name, "start_retry": 1, "max_new_attempts": 1}
        (self.root / "retry_manifest_v20.json").write_text(json.dumps({"version": "test", "jobs": [job]}), encoding="utf-8")
        retry = self.root / "outputs" / "dsT_A_v20_01_r1.json"
        write_json(retry); write_review(retry, "过")
        data = report.build_data(self.root, self.checker)
        self.assertEqual([retry.name], data["ds_retry_passes"])
        self.assertIn("DS 重试裁判过件：1", report.render_markdown(data))


if __name__ == "__main__":
    unittest.main()
