import csv
import json
import tempfile
import unittest
from pathlib import Path

import run_ds


class PlanningTests(unittest.TestCase):
    def test_build_jobs_has_all_three_batches_and_exact_settings(self):
        jobs = run_ds.build_jobs()
        self.assertEqual(36, len(jobs))
        self.assertEqual("ds_A_v18_01.json", jobs[0].filename)
        self.assertEqual("ds_D_v18_02.json", jobs[7].filename)
        self.assertFalse(any(job.thinking for job in jobs[:8]))
        self.assertEqual("dsT_A_v18_01.json", jobs[8].filename)
        self.assertEqual("dsT_D_v18_02.json", jobs[15].filename)
        self.assertTrue(all(job.thinking for job in jobs[8:16]))
        self.assertTrue(all(job.reasoning_effort == "high" for job in jobs[8:16]))
        self.assertEqual("ds_B_div_01.json", jobs[16].filename)
        self.assertEqual("ds_B_div_20.json", jobs[-1].filename)
        self.assertFalse(any(job.thinking for job in jobs[16:]))

    def test_estimate_uses_utf8_upper_bound_and_full_maximum_output(self):
        estimate = run_ds.estimate_call_cost("中a", max_tokens=10)
        expected_prompt = 4 + run_ds.PROMPT_OVERHEAD_TOKENS
        expected = (expected_prompt * run_ds.INPUT_PRICE + 10 * run_ds.OUTPUT_PRICE) / 1_000_000
        self.assertAlmostEqual(expected, estimate)

    def test_thinking_cli_filters_batches_but_diversity_remains_off(self):
        self.assertEqual(28, len(run_ds.select_jobs("off")))
        self.assertFalse(any(job.thinking for job in run_ds.select_jobs("off")))
        self.assertEqual(8, len(run_ds.select_jobs("on")))
        self.assertTrue(all(job.thinking for job in run_ds.select_jobs("on")))
        self.assertEqual(36, len(run_ds.select_jobs("both")))

    def test_budget_gate_rejects_a_full_call_before_sending(self):
        with self.assertRaises(run_ds.BudgetExceeded):
            run_ds.require_call_budget(spent=4.99, call_ceiling=0.02, budget=5.0)


class RequestAndRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "outputs").mkdir()
        (self.root / "inputs").mkdir()
        (self.root / "DM-skill-开局生成-v1.8.md").write_text("SYSTEM", encoding="utf-8")
        (self.root / "inputs" / "input_A.json").write_text('{"input":"A"}', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def response(content, *, prompt=10, completion=20, reasoning=7):
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "completion_tokens_details": {"reasoning_tokens": reasoning},
            },
        }

    def test_payload_has_independent_messages_and_no_json_mode(self):
        payload = run_ds.make_payload("SYS", "USER", thinking=True, max_tokens=16000)
        self.assertEqual("deepseek-v4-flash", payload["model"])
        self.assertEqual(0.8, payload["temperature"])
        self.assertEqual(16000, payload["max_tokens"])
        self.assertEqual({"type": "enabled"}, payload["thinking"])
        self.assertEqual("high", payload["reasoning_effort"])
        self.assertEqual(
            [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
            payload["messages"],
        )
        self.assertNotIn("response_format", payload)

    def test_invalid_json_is_preserved_and_retry_gets_r_suffix(self):
        replies = iter([
            self.response("not json", prompt=3, completion=4, reasoning=0),
            self.response('{"ok": true}', prompt=5, completion=6, reasoning=2),
        ])
        payloads = []

        def transport(_key, payload):
            payloads.append(payload)
            return next(replies)

        job = run_ds.Job("A", "ds_A_v18_01.json", False, "")
        result = run_ds.run_job(
            job,
            root=self.root,
            keys=["secret-one", "secret-two"],
            transport=transport,
            max_tokens=100,
            budget=5.0,
            spent=0.0,
        )
        self.assertEqual("ds_A_v18_01_r1.json", result.output_path.name)
        self.assertEqual("not json", (self.root / "outputs" / "ds_A_v18_01.json").read_text(encoding="utf-8"))
        self.assertEqual({"ok": True}, json.loads(result.output_path.read_text(encoding="utf-8")))
        self.assertEqual(2, len(payloads))

        with (self.root / "usage_log.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(2, len(rows))
        self.assertEqual(run_ds.USAGE_FIELDS, list(rows[0]))
        self.assertEqual("deepseek-v4-flash", rows[1]["model"])
        self.assertEqual("false", rows[1]["thinking"])
        self.assertEqual("", rows[1]["reasoning_effort"])
        self.assertEqual("5", rows[1]["prompt_token"])
        self.assertEqual("6", rows[1]["completion_token"])
        self.assertEqual("2", rows[1]["reasoning_token"])

    def test_three_retries_means_four_preserved_attempts(self):
        def transport(_key, _payload):
            return self.response("bad")

        job = run_ds.Job("A", "ds_A_v18_01.json", False, "")
        with self.assertRaises(run_ds.JobFailed):
            run_ds.run_job(
                job,
                root=self.root,
                keys=["secret"],
                transport=transport,
                max_tokens=100,
                budget=5.0,
                spent=0.0,
            )
        names = sorted(path.name for path in (self.root / "outputs").glob("*.json"))
        self.assertEqual(
            ["ds_A_v18_01.json", "ds_A_v18_01_r1.json", "ds_A_v18_01_r2.json", "ds_A_v18_01_r3.json"],
            names,
        )


class ReportingTests(unittest.TestCase):
    def test_classify_check_line_separates_history_and_v18(self):
        self.assertEqual("history_v11", run_ds.classify_check_line("挂 fable_A_v11_01.json: bad"))
        self.assertEqual("v18", run_ds.classify_check_line("挂 haiku_D_v18_02.json: bad"))
        self.assertEqual("other", run_ds.classify_check_line("过 fable_A_v16_01.json: 检查通过"))

    def test_usage_summary_totals_tokens_cost_and_average_latency(self):
        rows = [
            {"prompt_token": "10", "completion_token": "20", "reasoning_token": "5", "latency_seconds": "2", "cost_usd": "0.1"},
            {"prompt_token": "1", "completion_token": "2", "reasoning_token": "0", "latency_seconds": "4", "cost_usd": "0.2"},
        ]
        summary = run_ds.summarize_usage(rows)
        self.assertEqual(11, summary.prompt_tokens)
        self.assertEqual(22, summary.completion_tokens)
        self.assertEqual(5, summary.reasoning_tokens)
        self.assertAlmostEqual(0.3, summary.cost_usd)
        self.assertAlmostEqual(3.0, summary.average_latency)


if __name__ == "__main__":
    unittest.main()
