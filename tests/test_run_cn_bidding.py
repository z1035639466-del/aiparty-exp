import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_cn_bidding as runner


class ChinaBiddingRunnerTests(unittest.TestCase):
    def test_fixed_models_and_three_runs(self):
        self.assertEqual(
            [
                "MiniMax-M2.7",
                "kimi-k2.6",
                "glm-5.1",
                "qwen3.7-plus",
                "deepseek-v4-pro",
            ],
            [provider.requested_model for provider in runner.PROVIDERS],
        )
        self.assertEqual(3, runner.RUNS_PER_PROVIDER)
        self.assertEqual(Path("docs/specs/DM-skill-v2.0.md"), runner.SYSTEM_PATH.relative_to(runner.ROOT))
        self.assertEqual(Path("inputs/input_B.json"), runner.INPUT_PATH.relative_to(runner.ROOT))

    def test_payload_uses_exact_messages_and_provider_controls(self):
        provider = runner.PROVIDERS[0]
        payload = runner.build_payload(provider, "SYSTEM", "USER", 16000)
        self.assertEqual(
            [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}],
            payload["messages"],
        )
        self.assertEqual("MiniMax-M2.7", payload["model"])
        self.assertTrue(payload["reasoning_split"])
        self.assertNotIn("api_key", payload)

        kimi_payload = runner.build_payload(runner.PROVIDERS[1], "SYSTEM", "USER", 16000)
        self.assertEqual(1.0, kimi_payload["temperature"])

    def test_usage_preserves_unavailable_and_computes_cache_discount(self):
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "prompt_tokens_details": {"cached_tokens": 400},
            }
        }
        usage = runner.extract_usage(response)
        self.assertEqual("unavailable", usage["reasoning"])
        self.assertEqual(400, usage["cached"])
        provider = runner.PROVIDERS[0]
        expected = ((600 * 2.1) + (400 * 0.42) + (500 * 8.4)) / 1_000_000
        self.assertAlmostEqual(expected, runner.calculate_cost(provider, usage))

    def test_run_one_writes_content_verbatim_and_never_logs_key(self):
        response = {
            "model": "provider-model-id",
            "choices": [{"message": {"content": "  {\"ok\":true}\n"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        calls = []

        def transport(key, provider, payload):
            calls.append((key, provider, payload))
            return response, json.dumps(response)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "outputs"
            usage_path = root / "usage.csv"
            with (
                mock.patch.object(runner, "OUTPUTS_DIR", outputs),
                mock.patch.object(runner, "USAGE_PATH", usage_path),
            ):
                path = runner.run_one(
                    runner.PROVIDERS[0],
                    "secret-key-material",
                    1,
                    system_text="SYSTEM",
                    user_text="USER",
                    max_tokens=100,
                    transport=transport,
                )
            self.assertEqual("  {\"ok\":true}\n", path.read_text(encoding="utf-8"))
            with usage_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("provider-model-id", rows[0]["response_model"])
            self.assertEqual("unavailable", rows[0]["reasoning_token"])
            self.assertNotIn("secret-key-material", usage_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(calls))

    def test_transport_error_is_one_attempt_and_keeps_raw_body(self):
        attempts = []

        def transport(*args):
            attempts.append(args)
            raise runner.TransportError("HTTP 429", '{"error":"rate"}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(runner, "OUTPUTS_DIR", root / "outputs"),
                mock.patch.object(runner, "USAGE_PATH", root / "usage.csv"),
            ):
                path = runner.run_one(
                    runner.PROVIDERS[0],
                    "secret",
                    1,
                    system_text="SYSTEM",
                    user_text="USER",
                    max_tokens=100,
                    transport=transport,
                )
            self.assertEqual('{"error":"rate"}', path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(attempts))


if __name__ == "__main__":
    unittest.main()
