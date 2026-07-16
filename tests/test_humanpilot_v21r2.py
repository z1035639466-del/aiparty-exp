from __future__ import annotations

import json
import unittest
from pathlib import Path

import check


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
PILOTS = [
    OUTPUTS / "humanpilot_01_haiku_A_v21r2.json",
    OUTPUTS / "humanpilot_02_sonnet_A_v21r2_m1.json",
    OUTPUTS / "humanpilot_03_sonnet_C_v21r2_m1.json",
]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


class HumanPilotV21R2Tests(unittest.TestCase):
    def test_three_pilots_pass_check_and_sidecars_match(self) -> None:
        whitelist = check.load_whitelist()
        for path in PILOTS:
            result = check.check_document(load(path), whitelist)
            self.assertEqual(result.errors, [], path.name)
            sidecar = path.with_suffix(".warnings.json")
            if result.warnings:
                self.assertTrue(sidecar.exists(), path.name)
                payload = load(sidecar)
                self.assertEqual(payload["spec_version"], "v2.1.1")
                self.assertEqual(payload["warnings"], list(result.warnings))
            else:
                self.assertFalse(sidecar.exists(), path.name)

    def test_lead_pilot_is_exact_three_green_copy(self) -> None:
        source = OUTPUTS / "haiku_A_v21r2_01_r1.json"
        self.assertEqual(load(PILOTS[0]), load(source))

    def test_sonnet_a_has_consensus_input_for_dice(self) -> None:
        doc = load(PILOTS[1])
        rule = next(item for item in doc["rules"] if item["flavor_name"] == "开盅共识确认")
        self.assertEqual(rule["mechanic"], "判定")
        self.assertEqual(rule["params"]["source"], "consensus")
        self.assertIn("翻盘失败", rule["params"]["on"]["不过"]["scoring_ref"])
        self.assertTrue(any(item["event"] == "开盅安全" for item in doc["settlement"]["scoring"]))

    def test_sonnet_c_opt_out_does_not_erase_booked_score(self) -> None:
        doc = load(PILOTS[2])
        text = doc["rules"][6]["plain_rule"] + doc["safety_note"]
        self.assertNotIn("轻扣分直接免掉", text)
        self.assertIn("轻扣分照常保留", text)
        self.assertIn("赛内记分照常保留", text)


if __name__ == "__main__":
    unittest.main()
