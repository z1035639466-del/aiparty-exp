from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_v21r2_report", ROOT / "build_v21r2_report.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class BuildV21R2ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = MODULE.build_data()

    def test_frozen_counts_and_protocol(self) -> None:
        passes = {row["key"]: row for row in self.data["pass"]}
        structures = {row["key"]: row for row in self.data["structure"]}

        self.assertEqual([passes[key]["pass_at_1"]["passed"] for key in MODULE.MODEL_ORDER], [0, 0, 0, 0])
        self.assertEqual([passes[key]["pass_at_2"]["passed"] for key in MODULE.MODEL_ORDER], [0, 1, 0, 0])
        self.assertEqual([structures[key]["first_pass"]["passed"] for key in MODULE.MODEL_ORDER], [7, 2, 0, 15])
        self.assertEqual([structures[key]["after_structure_repair"]["passed"] for key in MODULE.MODEL_ORDER], [8, 3, 1, 16])
        self.assertEqual([structures[key]["terminal_artifact"]["passed"] for key in MODULE.MODEL_ORDER], [7, 2, 1, 16])

        self.assertEqual(self.data["protocol_audit"], {
            "candidate_json": 79,
            "warning_sidecars": 58,
            "sidecar_mismatch": 0,
            "invalid_candidate_reviews": 0,
            "main_first_pass_structure_pass_and_reviewed": 24,
            "retry_structure_pass_and_reviewed": 11,
            "terminal_structure_pass_and_reviewed": 26,
            "terminal_structure_reject_no_judgment": 18,
        })

    def test_b20_prop_and_compiler_ledgers(self) -> None:
        b20 = self.data["b20"]
        nonzero = {
            row["family_id"]: row["count"]
            for row in b20["family_frequency_41"] if row["count"]
        }
        self.assertEqual(len(b20["family_frequency_41"]), 41)
        self.assertEqual(nonzero, {4: 1, 6: 1, 7: 4, 8: 2, 40: 3, 41: 5})
        self.assertEqual(b20["summary"]["terminal_structure"]["passed"], 16)
        self.assertEqual(b20["summary"]["shape_frequency"], {"其他": 15, "社推": 1})

        props = self.data["prop_health_first_pass"]
        self.assertEqual([props[key]["canonical_coverage"] for key in MODULE.MODEL_ORDER], [11, 6, 11, 11])
        self.assertEqual([props[key]["true_dead_prop"] for key in MODULE.MODEL_ORDER], [7, 3, 9, 19])
        self.assertEqual(self.data["probe_A_prime"]["positive"], 0)
        self.assertEqual(self.data["compiler"]["first_three_green"], "haiku_A_v21r2_01_r1.json")
        self.assertEqual(self.data["compiler"]["candidate_coverage"], "1/1")

    def test_usage_totals(self) -> None:
        total = self.data["usage"]["api"]["total"]
        self.assertEqual((total["calls"], total["success"], total["failed"]), (16, 15, 1))
        self.assertEqual(total["prompt_tokens"], 116405)
        self.assertEqual(total["completion_tokens"], 156982)
        self.assertEqual(total["reasoning_tokens_subset_of_completion"], 135532)
        self.assertEqual(total["total_tokens_prompt_plus_completion"], 273387)
        self.assertEqual(total["cost_usd"], 0.06025166)


if __name__ == "__main__":
    unittest.main()
