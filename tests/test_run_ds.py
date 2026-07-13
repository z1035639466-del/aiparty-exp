import csv
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import run_ds


class PlanningTests(unittest.TestCase):
    def test_build_jobs_has_frozen_v20_thinking_and_diversity_batches(self):
        jobs = run_ds.build_jobs()
        self.assertEqual(28, len(jobs))
        self.assertEqual("dsT_A_v20_01.json", jobs[0].filename)
        self.assertEqual("dsT_D_v20_02.json", jobs[7].filename)
        self.assertTrue(all(job.thinking for job in jobs[:8]))
        self.assertTrue(all(job.reasoning_effort == "high" for job in jobs[:8]))
        self.assertEqual("ds_B_div_v20_01.json", jobs[8].filename)
        self.assertEqual("ds_B_div_v20_20.json", jobs[-1].filename)
        self.assertFalse(any(job.thinking for job in jobs[8:]))

    def test_estimate_uses_utf8_upper_bound_and_full_maximum_output(self):
        estimate = run_ds.estimate_call_cost("中a", max_tokens=10)
        expected_prompt = 4 + run_ds.PROMPT_OVERHEAD_TOKENS
        expected = (expected_prompt * run_ds.INPUT_PRICE + 10 * run_ds.OUTPUT_PRICE) / 1_000_000
        self.assertAlmostEqual(expected, estimate)

    def test_thinking_cli_filters_batches_but_diversity_remains_off(self):
        self.assertEqual(20, len(run_ds.select_jobs("off")))
        self.assertFalse(any(job.thinking for job in run_ds.select_jobs("off")))
        self.assertEqual(8, len(run_ds.select_jobs("on")))
        self.assertTrue(all(job.thinking for job in run_ds.select_jobs("on")))
        self.assertEqual(28, len(run_ds.select_jobs("both")))

    def test_successful_retry_maps_to_its_logical_filename_for_resume(self):
        rows = [
            {"filename": "dsT_A_v20_01_r1.json", "status": "success"},
            {"filename": "ds_B_div_v20_01.json", "status": "failed"},
        ]
        self.assertEqual({"dsT_A_v20_01.json"}, run_ds.successful_job_filenames(rows))

    def test_budget_gate_rejects_a_full_call_before_sending(self):
        with self.assertRaises(run_ds.BudgetExceeded):
            run_ds.require_call_budget(spent=4.99, call_ceiling=0.02, budget=5.0)


class RequestAndRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "outputs").mkdir()
        (self.root / "inputs").mkdir()
        (self.root / "DM-skill-开局生成-v2.0.md").write_text("SYSTEM", encoding="utf-8")
        (self.root / "inputs" / "input_A.json").write_text('{"input":"A"}', encoding="utf-8")
        (self.root / "inputs" / "input_B.json").write_text('{"input":"B"}', encoding="utf-8")

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

        job = run_ds.Job("B", "ds_B_div_v20_01.json", False, "")
        result = run_ds.run_job(
            job,
            root=self.root,
            keys=["secret-one", "secret-two"],
            transport=transport,
            max_tokens=100,
            budget=5.0,
            spent=0.0,
        )
        self.assertEqual("ds_B_div_v20_01_r1.json", result.output_path.name)
        self.assertEqual("not json", (self.root / "outputs" / "ds_B_div_v20_01.json").read_text(encoding="utf-8"))
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

        job = run_ds.Job("A", "dsT_A_v20_01.json", True, "high")
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
            ["dsT_A_v20_01.json", "dsT_A_v20_01_r1.json", "dsT_A_v20_01_r2.json", "dsT_A_v20_01_r3.json"],
            names,
        )


class FeedbackRetryTests(RequestAndRetryTests):
    def feedback_job(self, **changes):
        values = {
            "group": "main_structure",
            "input_name": "A",
            "source": "dsT_A_v20_01.json",
            "start_retry": 1,
            "max_new_attempts": 1,
            "feedback_kind": "check",
            "feedback": "rules[1] bad",
            "model": "dsT",
        }
        values.update(changes)
        return run_ds.FeedbackJob(**values)

    def test_feedback_prompt_has_original_input_and_exactly_one_feedback_block(self):
        seen = []

        def transport(_key, payload):
            seen.append(payload)
            return self.response('{"ok": true}')

        controller = run_ds.BudgetController(0.0, 5.0)
        with mock.patch("run_ds.check_errors", return_value=[]):
            result = run_ds.run_feedback_job(
                self.feedback_job(), root=self.root, keys=["secret"], transport=transport,
                max_tokens=100, budget_controller=controller,
            )
        self.assertTrue(result.success)
        messages = seen[0]["messages"]
        self.assertEqual("SYSTEM", messages[0]["content"])
        self.assertTrue(messages[1]["content"].startswith('{"input":"A"}'))
        self.assertEqual(1, messages[1]["content"].count("【check.py 报错原文】"))
        self.assertEqual(1, messages[1]["content"].count("rules[1] bad"))

    def test_retry_name_strips_existing_suffix_instead_of_double_r(self):
        path = run_ds.feedback_attempt_path(self.root / "outputs", "dsT_A_v20_01_r1.json", 2)
        self.assertEqual("dsT_A_v20_01_r2.json", path.name)

    def test_occupied_explicit_suffix_makes_zero_calls(self):
        occupied = self.root / "outputs" / "dsT_A_v20_01_r1.json"
        occupied.write_text("KEEP", encoding="utf-8")
        calls = []
        controller = run_ds.BudgetController(0.0, 5.0)
        result = run_ds.run_feedback_job(
            self.feedback_job(), root=self.root, keys=["secret"],
            transport=lambda *_: calls.append(1), max_tokens=100, budget_controller=controller,
        )
        self.assertFalse(result.success)
        self.assertEqual([], calls)
        self.assertEqual("KEEP", occupied.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "usage_log.csv").exists())

    def test_b_second_attempt_uses_new_check_errors_and_never_creates_r3(self):
        payloads = []

        def transport(_key, payload):
            payloads.append(payload)
            return self.response('{"ok": true}')

        job = self.feedback_job(
            group="b_structure", source="ds_B_div_v20_02.json", model="ds_B_div",
            input_name="B", max_new_attempts=5,
        )
        controller = run_ds.BudgetController(0.0, 5.0)
        with mock.patch("run_ds.check_errors", side_effect=[["new r1 error"], []]):
            result = run_ds.run_feedback_job(
                job, root=self.root, keys=["secret"], transport=transport,
                max_tokens=100, budget_controller=controller,
            )
        self.assertTrue(result.success)
        self.assertEqual(2, len(payloads))
        self.assertNotIn("new r1 error", payloads[0]["messages"][1]["content"])
        self.assertIn("new r1 error", payloads[1]["messages"][1]["content"])
        self.assertTrue((self.root / "outputs" / "ds_B_div_v20_02_r2.json").exists())
        self.assertFalse((self.root / "outputs" / "ds_B_div_v20_02_r3.json").exists())

    def test_manifest_selects_only_api_and_caps_attempts(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"jobs": [
            {"group": "b_structure", "channel": "api", "model": "ds_B_div", "input_name": "B",
             "source": "ds_B_div_v20_01.json", "start_retry": 1, "max_new_attempts": 9,
             "feedback_kind": "check", "feedback": "bad"},
            {"group": "main_structure", "channel": "web", "model": "haiku", "input_name": "A",
             "source": "haiku_A.json", "start_retry": 1, "max_new_attempts": 1,
             "feedback_kind": "check", "feedback": "bad"},
        ]}), encoding="utf-8")
        jobs = run_ds.load_feedback_jobs(manifest)
        self.assertEqual(1, len(jobs))
        self.assertEqual(2, jobs[0].max_new_attempts)

    def test_concurrent_usage_append_writes_one_header(self):
        path = self.root / "usage_log.csv"
        base = {field: "x" for field in run_ds.USAGE_FIELDS}
        threads = [threading.Thread(target=run_ds.append_usage, args=(path, {**base, "filename": str(i)}))
                   for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, sum(line.startswith("filename,model,") for line in lines))
        with path.open(encoding="utf-8", newline="") as handle:
            self.assertEqual(20, len(list(csv.DictReader(handle))))


class ReportingTests(unittest.TestCase):
    def test_check_subprocess_environment_forces_utf8_output(self):
        environment = run_ds.check_subprocess_environment({"EXISTING": "value"})
        self.assertEqual("value", environment["EXISTING"])
        self.assertEqual("utf-8", environment["PYTHONIOENCODING"])

    def test_classify_check_line_separates_history_and_v20(self):
        self.assertEqual("history_v11", run_ds.classify_check_line("挂 fable_A_v11_01.json: bad"))
        self.assertEqual("v20", run_ds.classify_check_line("挂 haiku_D_v20_02.json: bad"))
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
