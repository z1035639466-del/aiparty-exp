import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import run_ugc_collection as ugc


def make_record(**overrides):
    record = {
        "track": "live",
        "platform": "小红书",
        "source_url": "https://example.com/post/1",
        "captured_at": "2026-07-16T12:30:00+08:00",
        "published_at": None,
        "language": "zh-CN",
        "region": "CN",
        "query": "局头 喝酒游戏",
        "creator": "测试创作者",
        "role_terms": [{"term": "局头", "role": "host", "status": "verified"}],
        "atom_type": "screen_self_ref",
        "title": "手机举分",
        "mechanic": {
            "trigger": "主持人提出一个问题",
            "action": "所有人在手机上写下 1 到 10 的评分并同时举起",
            "resolution": "按评分差异触发下一轮讨论",
        },
        "safety": {
            "forced_drinking": False,
            "non_alcohol_alternative": "",
            "adult_level": "none",
            "refusal_guard": "",
        },
        "license": "UGC-reference-only",
        "evidence_note": "视频演示了同时亮出手机评分的玩法，已改写为机制步骤。",
        "tags": ["手机", "同时揭晓"],
    }
    for key, value in overrides.items():
        record[key] = value
    return record


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200, headers=None, url=None):
        super().__init__(payload)
        self.status = status
        self.headers = {"Content-Length": str(len(payload))} if headers is None else headers
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def geturl(self):
        return self._url


class FailingResponse:
    status = 200

    def __init__(self):
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _size):
        self.read_count += 1
        if self.read_count == 1:
            return b"partial"
        raise OSError("connection reset")


class FakeSocket:
    def __init__(self):
        self.timeout = None
        self.bound = None
        self.connected = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def bind(self, value):
        self.bound = value

    def connect(self, value):
        self.connected = value

    def close(self):
        self.closed = True


class RecordValidationTests(unittest.TestCase):
    def test_unicode_jsonl_round_trip_and_strict_validation(self):
        record = ugc.prepare_record(make_record())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "atoms.jsonl"
            ugc.write_jsonl(path, [record])
            loaded = ugc.load_jsonl(path)
            ugc.validate_located_records(loaded)
            self.assertEqual("局头", loaded[0].value["role_terms"][0]["term"])
            self.assertEqual(record, loaded[0].value)

    def test_error_includes_file_and_line(self):
        invalid = make_record()
        invalid.pop("source_url")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.jsonl"
            path.write_text(json.dumps(invalid, ensure_ascii=False) + "\n", encoding="utf-8")
            loaded = ugc.load_jsonl(path)
            with self.assertRaises(ugc.RecordValidationError) as caught:
                ugc.validate_located_records(loaded)
            self.assertIn(f"{path}:1", str(caught.exception))
            self.assertIn("source_url", str(caught.exception))

    def test_invalid_json_reports_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.jsonl"
            path.write_text("{}\n{broken\n", encoding="utf-8")
            with self.assertRaisesRegex(ugc.RecordFormatError, re.escape(f"{path}:2: invalid JSON")):
                ugc.load_jsonl(path)

    def test_forced_drinking_requires_non_alcohol_alternative(self):
        record = make_record(
            safety={
                "forced_drinking": True,
                "non_alcohol_alternative": "",
                "adult_level": "none",
                "refusal_guard": "",
            }
        )
        issues = ugc.record_issues(record, require_id=False)
        self.assertTrue(any("non-empty safety.non_alcohol_alternative" in issue for issue in issues))

    def test_yellow_or_adult_material_requires_refusal_guard(self):
        for level in ("yellow", "adult"):
            with self.subTest(level=level):
                record = make_record(
                    safety={
                        "forced_drinking": False,
                        "non_alcohol_alternative": "",
                        "adult_level": level,
                        "refusal_guard": "",
                    }
                )
                issues = ugc.record_issues(record, require_id=False)
                self.assertTrue(any("requires a non-empty safety.refusal_guard" in issue for issue in issues))

    def test_raw_caption_and_transcript_fields_are_rejected_recursively(self):
        record = make_record()
        record["mechanic"]["transcript"] = "copied words"
        record["raw_text"] = "copied post"
        issues = ugc.record_issues(record, require_id=False)
        self.assertTrue(any("$.mechanic.transcript" in issue for issue in issues))
        self.assertTrue(any("$.raw_text" in issue for issue in issues))

    def test_evidence_note_has_hard_length_limit(self):
        record = make_record(evidence_note="a" * (ugc.MAX_EVIDENCE_CHARS + 1))
        issues = ugc.record_issues(record, require_id=False)
        self.assertTrue(any("short rewritten fact" in issue for issue in issues))

    def test_stable_id_must_match_mechanism(self):
        prepared = ugc.prepare_record(make_record())
        prepared["id"] = "ugc_0000000000000000"
        issues = ugc.record_issues(prepared)
        self.assertTrue(any("expected ugc_" in issue for issue in issues))

    def test_schema_rejects_unknown_fields_at_every_object_level(self):
        record = make_record(unexpected="payload")
        record["role_terms"][0]["extra"] = True
        record["mechanic"]["extra"] = "payload"
        record["safety"]["extra"] = "payload"
        issues = ugc.record_issues(record, require_id=False)
        self.assertTrue(any("record contains unsupported fields" in issue for issue in issues))
        self.assertTrue(any("role_terms[0] contains unsupported fields" in issue for issue in issues))
        self.assertTrue(any("mechanic contains unsupported fields" in issue for issue in issues))
        self.assertTrue(any("safety contains unsupported fields" in issue for issue in issues))

    def test_schema_enforces_collection_counts_item_lengths_and_total_bytes(self):
        too_many_tags = make_record(tags=[f"tag-{index}" for index in range(ugc.MAX_TAGS + 1)])
        self.assertTrue(any("tags must contain at most" in issue for issue in ugc.record_issues(too_many_tags, require_id=False)))

        long_tag = make_record(tags=["x" * (ugc.MAX_TAG_CHARS + 1)])
        self.assertTrue(any("tags[0] must not exceed" in issue for issue in ugc.record_issues(long_tag, require_id=False)))

        source_url = "https://example.com/root"
        large = make_record(source_url=source_url)
        large["source_urls"] = [source_url] + [
            f"https://example.com/{index}/" + ("a" * 1800)
            for index in range(ugc.MAX_SOURCE_URLS - 1)
        ]
        self.assertTrue(any("UTF-8 bytes" in issue for issue in ugc.record_issues(large, require_id=False)))

    def test_validation_rejects_duplicate_ids_across_files(self):
        prepared = ugc.prepare_record(make_record())
        records = [
            ugc.LocatedRecord(prepared, Path("first.jsonl"), 1),
            ugc.LocatedRecord(dict(prepared), Path("second.jsonl"), 7),
        ]
        with self.assertRaisesRegex(ugc.RecordValidationError, "duplicate id"):
            ugc.validate_located_records(records)

    def test_load_jsonl_rejects_oversized_line_before_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "huge.jsonl"
            path.write_text(json.dumps({"payload": "x" * ugc.MAX_RECORD_BYTES}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ugc.RecordFormatError, "record exceeds"):
                ugc.load_jsonl(path)

    def test_load_jsonl_rejects_duplicate_object_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.jsonl"
            path.write_text('{"track":"live","track":"batch"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ugc.RecordFormatError, "duplicate JSON object key"):
                ugc.load_jsonl(path)


class NormalizationAndMergeTests(unittest.TestCase):
    def test_url_normalization_removes_tracking_fragment_without_reordering_query(self):
        url = "HTTPS://Example.COM:443/post/1/?b=2&utm_source=x&a=1#comments"
        self.assertEqual("https://example.com/post/1/?b=2&a=1", ugc.normalize_url(url))

    def test_url_with_embedded_credentials_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            ugc.normalize_url("https://user:secret@example.com/post")

    def test_xiaohongshu_url_drops_auth_and_uses_platform_canonical_url(self):
        raw = (
            "https://xiaohongshu.com/discovery/item/64a153190000000013034585/"
            "?xsec_token=SECRET&xsec_source=pc_feed&utm_source=share#comments"
        )
        canonical = "https://www.xiaohongshu.com/explore/64a153190000000013034585"
        self.assertEqual(canonical, ugc.normalize_url(raw))
        prepared = ugc.prepare_record(make_record(source_url=raw))
        self.assertEqual(canonical, prepared["source_url"])
        issues = ugc.record_issues(make_record(source_url=raw), require_id=False)
        self.assertTrue(any("source_url must be canonical" in issue for issue in issues))
        self.assertNotIn("SECRET", "\n".join(issues))

    def test_generic_auth_signature_parameters_are_removed(self):
        raw = "https://example.com/post?b=2&accessToken=secret&X-Amz-Signature=sig&a=1"
        self.assertEqual("https://example.com/post?b=2&a=1", ugc.normalize_url(raw))

    def test_secret_and_password_parameters_are_removed_and_manifest_rejects_them(self):
        for key in ("client_secret", "clientSecret", "password", "secret"):
            with self.subTest(key=key):
                url = f"https://example.com/post?{key}=SECRET&item=1"
                self.assertEqual("https://example.com/post?item=1", ugc.normalize_url(url))
                self.assertTrue(ugc._url_has_sensitive_query(url))

    def test_rfc3986_normalization_preserves_encoded_delimiters_and_ipv6(self):
        url = "HTTPS://[2001:4860:4860::8888]:443/a%2fb/%7e?q=a%2fb"
        self.assertEqual(
            "https://[2001:4860:4860::8888]/a%2Fb/~?q=a%2Fb",
            ugc.normalize_url(url),
        )

    def test_rfc3986_normalization_preserves_semantic_order_and_slashes(self):
        first = ugc.normalize_url("https://example.com/a/?step=1&step=2")
        second = ugc.normalize_url("https://example.com/a/?step=2&step=1")
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            ugc.normalize_url("https://example.com/a"),
            ugc.normalize_url("https://example.com/a/"),
        )
        self.assertEqual(
            "https://example.com/a/c",
            ugc.normalize_url("https://example.com/a/b/../c"),
        )

    def test_private_local_and_ambiguous_ip_urls_are_rejected(self):
        for url in (
            "http://localhost/a",
            "http://127.0.0.1/a",
            "http://169.254.1.1/a",
            "http://[::1]/a",
            "http://2130706433/a",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ugc.assert_public_url(url, resolve=False)

    def test_dns_resolution_rejects_private_answer(self):
        def resolver(_host, _port, **_kwargs):
            return [(2, 1, 6, "", ("10.0.0.8", 443))]

        with self.assertRaisesRegex(ValueError, "resolves to a private"):
            ugc.assert_public_url("https://example.test/a", resolver=resolver)

    def test_same_mechanism_across_sources_merges_provenance_and_stricter_safety(self):
        first = make_record(source_url="https://example.com/post/1?utm_source=feed")
        second = make_record(
            track="batch",
            platform="公开视频库",
            source_url="https://archive.example.org/item/2#part",
            title="亮手机分数",
            evidence_note="归档页也描述了玩家同时展示手机分数的变体。",
            license="CC-BY-4.0",
            role_terms=[{"term": "host", "role": "host", "status": "candidate"}],
            safety={
                "forced_drinking": True,
                "non_alcohol_alternative": "改为喝水或得一枚标记",
                "adult_level": "yellow",
                "refusal_guard": "任何玩家可无条件跳过",
            },
            tags=["同步", "手机"],
        )
        merged = ugc.merge_records([second, first])
        self.assertEqual(1, len(merged))
        atom = merged[0]
        self.assertEqual(
            {"https://example.com/post/1", "https://archive.example.org/item/2"},
            set(atom["source_urls"]),
        )
        self.assertEqual(2, len(atom["role_terms"]))
        self.assertEqual(["CC-BY-4.0", "UGC-reference-only"], atom["licenses"])
        self.assertEqual(2, len(atom["evidence_notes"]))
        self.assertTrue(atom["safety"]["forced_drinking"])
        self.assertEqual("yellow", atom["safety"]["adult_level"])
        self.assertEqual("改为喝水或得一枚标记", atom["safety"]["non_alcohol_alternative"])
        self.assertEqual("任何玩家可无条件跳过", atom["safety"]["refusal_guard"])
        self.assertEqual(merged, ugc.merge_records(merged), "merge must be idempotent")

    def test_two_different_mechanisms_from_same_url_are_both_retained(self):
        first = make_record()
        second = make_record(
            title="另一条机制",
            mechanic={
                "trigger": "音乐停止",
                "action": "所有人立刻定格",
                "resolution": "最后移动的人获得一枚标记",
            },
        )
        merged = ugc.merge_records([first, second])
        self.assertEqual(2, len(merged))
        self.assertEqual(2, len({record["id"] for record in merged}))

    def test_case_and_whitespace_do_not_change_mechanism_fingerprint(self):
        first = make_record(
            atom_type="other",
            mechanic={"trigger": " Host Says Go ", "action": "Players   CLAP", "resolution": "Next round"},
        )
        second = make_record(
            atom_type="other",
            mechanic={"trigger": "host says go", "action": "players clap", "resolution": "next round"},
        )
        self.assertEqual(1, len(ugc.merge_records([first, second])))


class ReportTests(unittest.TestCase):
    def test_report_counts_source_urls_and_safety_dimensions(self):
        first = ugc.prepare_record(make_record())
        second = ugc.prepare_record(
            make_record(
                source_url="https://example.org/2",
                track="batch",
                platform="Wikimedia",
                language="en",
                atom_type="song_chain",
                title="Song chain",
                mechanic={
                    "trigger": "A player sings a line",
                    "action": "The next player continues with a matching song",
                    "resolution": "A miss passes the lead clockwise",
                },
                safety={
                    "forced_drinking": True,
                    "non_alcohol_alternative": "Use a point token",
                    "adult_level": "adult",
                    "refusal_guard": "Skip without penalty",
                },
            )
        )
        report = ugc.build_report([first, second])
        self.assertIn("- Records: 2", report)
        self.assertIn("- Source URLs: 2", report)
        self.assertIn("- Forced-drinking atoms: 1", report)
        self.assertIn("- Yellow/adult atoms: 1", report)
        self.assertIn("| live | 1 |", report)
        self.assertIn("| batch | 1 |", report)


class ManifestTests(unittest.TestCase):
    def write_manifest(self, root, sources):
        path = Path(root) / "manifest.json"
        path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
        return path

    def test_manifest_requires_license_and_safe_unique_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_manifest(
                temporary,
                [
                    {"id": "one", "url": "https://example.com/a", "filename": "same.bin", "license": "CC0"},
                    {"id": "two", "url": "https://example.com/b", "filename": "same.bin", "license": "CC0"},
                ],
            )
            with self.assertRaisesRegex(ugc.ManifestError, "duplicate cache name"):
                ugc.load_batch_manifest(path)

            path = self.write_manifest(
                temporary,
                [{"id": "one", "url": "https://example.com/a", "filename": "../escape", "license": "CC0"}],
            )
            with self.assertRaisesRegex(ugc.ManifestError, "unsafe cache filename"):
                ugc.load_batch_manifest(path)

            path = self.write_manifest(temporary, [{"id": "one", "url": "https://example.com/a"}])
            with self.assertRaisesRegex(ugc.ManifestError, "license"):
                ugc.load_batch_manifest(path)

    def test_manifest_accepts_supported_source_kinds_and_checksum(self):
        digest = hashlib.sha256(b"payload").hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_manifest(
                temporary,
                [
                    {
                        "id": "namuwiki",
                        "kind": "huggingface",
                        "url": "https://example.com/data.parquet",
                        "license": "CC-BY-NC-SA",
                        "sha256": digest,
                    }
                ],
            )
            items = ugc.load_batch_manifest(path)
            self.assertEqual("data.parquet", items[0].filename)
            self.assertEqual(digest, items[0].sha256)

    def test_manifest_normalizes_then_deduplicates_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_manifest(
                temporary,
                [
                    {
                        "id": "one",
                        "url": "HTTPS://Example.COM/a/?utm_source=feed",
                        "filename": "one.bin",
                        "license": "CC0",
                    },
                    {
                        "id": "two",
                        "url": "https://example.com/a/",
                        "filename": "two.bin",
                        "license": "CC0",
                    },
                ],
            )
            with self.assertRaisesRegex(ugc.ManifestError, "duplicate canonical URL"):
                ugc.load_batch_manifest(path)

    def test_manifest_rejects_auth_parameters_private_hosts_and_unknown_fields(self):
        cases = [
            ({"url": "https://example.com/a?xsec_token=secret"}, "authentication"),
            ({"url": "https://example.com/a?client_secret=secret"}, "authentication"),
            ({"url": "http://127.0.0.1/a"}, "private"),
            ({"url": "https://example.com/a", "extra": True}, "unsupported fields"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for index, (overrides, message) in enumerate(cases):
                with self.subTest(overrides=overrides):
                    source = {
                        "id": f"item-{index}",
                        "url": "https://example.com/a",
                        "filename": f"item-{index}.bin",
                        "license": "CC0",
                        **overrides,
                    }
                    path = self.write_manifest(temporary, [source])
                    with self.assertRaisesRegex(ugc.ManifestError, message):
                        ugc.load_batch_manifest(path)

    def test_manifest_rejects_windows_names_trailing_names_and_part_collisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            for filename in ("CON.txt", "report. ", "report."):
                with self.subTest(filename=filename):
                    path = self.write_manifest(
                        temporary,
                        [{"id": "one", "url": "https://example.com/a", "filename": filename, "license": "CC0"}],
                    )
                    with self.assertRaises(ugc.ManifestError):
                        ugc.load_batch_manifest(path)

            path = self.write_manifest(
                temporary,
                [
                    {"id": "one", "url": "https://example.com/a", "filename": "data", "license": "CC0"},
                    {"id": "two", "url": "https://example.com/b", "filename": "data.part", "license": "CC0"},
                ],
            )
            with self.assertRaisesRegex(ugc.ManifestError, "part path collision"):
                ugc.load_batch_manifest(path)

    def test_manifest_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(
                '{"sources":[{"id":"one","id":"two","url":"https://example.com/a","license":"CC0"}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ugc.ManifestError, "duplicate JSON object key"):
                ugc.load_batch_manifest(path)


class DownloadTests(unittest.TestCase):
    def test_arbitrary_custom_opener_is_disabled_without_explicit_test_opt_in(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        called = []

        def opener(_request, **_kwargs):
            called.append(True)
            return FakeResponse(b"payload")

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ugc.DownloadError, "custom opener is disabled"):
                ugc.download_item(item, Path(temporary), opener=opener)
        self.assertEqual([], called)

    def test_success_is_atomically_published_without_part_file(self):
        payload = b"complete payload"
        checksum = hashlib.sha256(payload).hexdigest()
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0", sha256=checksum)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            calls = []

            def opener(request, **_kwargs):
                calls.append(request)
                return FakeResponse(payload)

            result = ugc.download_item(item, cache, opener=opener, allow_test_opener=True)
            self.assertEqual("downloaded", result.status)
            self.assertEqual(payload, (cache / "one.bin").read_bytes())
            self.assertFalse((cache / "one.bin.part").exists())
            self.assertEqual(1, len(calls))

    def test_partial_transport_failure_leaves_only_part_file(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(_request, **_kwargs):
                return FailingResponse()

            with self.assertRaisesRegex(ugc.DownloadError, "connection reset"):
                ugc.download_item(
                    item, cache, opener=opener, allow_test_opener=True, chunk_size=4
                )
            self.assertEqual(b"partial", (cache / "one.bin.part").read_bytes())
            self.assertFalse((cache / "one.bin").exists())

    def test_resume_appends_206_response_and_sends_range_header(self):
        payload = b"abcdef"
        checksum = hashlib.sha256(payload).hexdigest()
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0", sha256=checksum)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin.part").write_bytes(b"abc")
            requests = []

            def opener(request, **_kwargs):
                requests.append(request)
                return FakeResponse(
                    b"def",
                    status=206,
                    headers={"Content-Length": "3", "Content-Range": "bytes 3-5/6"},
                )

            result = ugc.download_item(
                item, cache, resume=True, opener=opener, allow_test_opener=True
            )
            self.assertEqual("downloaded", result.status)
            self.assertEqual(b"abcdef", (cache / "one.bin").read_bytes())
            self.assertEqual("bytes=3-", requests[0].get_header("Range"))

    def test_resume_restarts_when_server_ignores_range(self):
        payload = b"new whole file"
        checksum = hashlib.sha256(payload).hexdigest()
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0", sha256=checksum)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin.part").write_bytes(b"stale prefix")

            def opener(_request, **_kwargs):
                return FakeResponse(payload, status=200)

            ugc.download_item(
                item, cache, resume=True, opener=opener, allow_test_opener=True
            )
            self.assertEqual(payload, (cache / "one.bin").read_bytes())

    def test_resume_without_checksum_discards_unverifiable_partial(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin.part").write_bytes(b"stale prefix")

            def opener(request, **_kwargs):
                self.assertIsNone(request.get_header("Range"))
                return FakeResponse(b"new whole file")

            ugc.download_item(
                item, cache, resume=True, opener=opener, allow_test_opener=True
            )
            self.assertEqual(b"new whole file", (cache / "one.bin").read_bytes())

    def test_download_sends_transparent_user_agent_and_safe_accept_headers(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(request, **_kwargs):
                headers = {key.casefold(): value for key, value in request.header_items()}
                self.assertEqual(ugc.DEFAULT_USER_AGENT, headers["user-agent"])
                self.assertIn("application/json", headers["accept"])
                self.assertEqual("en-US,en;q=0.8", headers["accept-language"])
                self.assertEqual("identity", headers["accept-encoding"])
                return FakeResponse(b"public payload")

            result = ugc.download_item(
                item, cache, opener=opener, allow_test_opener=True
            )
            self.assertEqual("downloaded", result.status)
            self.assertEqual(b"public payload", (cache / "one.bin").read_bytes())

    def test_resume_skips_verified_final_without_network(self):
        payload = b"already complete"
        checksum = hashlib.sha256(payload).hexdigest()
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0", sha256=checksum)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin").write_bytes(payload)

            def opener(_request, **_kwargs):
                self.fail("network must not be called")

            result = ugc.download_item(
                item, cache, resume=True, opener=opener, allow_test_opener=True
            )
            self.assertEqual("skipped", result.status)

    def test_checksum_failure_resets_bad_part_and_does_not_publish(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0", sha256="0" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(_request, **_kwargs):
                return FakeResponse(b"bad payload")

            with self.assertRaisesRegex(ugc.DownloadError, "sha256 mismatch"):
                ugc.download_item(item, cache, opener=opener, allow_test_opener=True)
            self.assertFalse((cache / "one.bin.part").exists())
            self.assertFalse((cache / "one.bin").exists())

    def test_content_length_mismatch_resets_part(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(_request, **_kwargs):
                return FakeResponse(b"short", headers={"Content-Length": "99"})

            with self.assertRaisesRegex(ugc.DownloadError, "response length mismatch"):
                ugc.download_item(item, cache, opener=opener, allow_test_opener=True)
            self.assertFalse((cache / "one.bin.part").exists())
            self.assertFalse((cache / "one.bin").exists())

    def test_invalid_content_range_resets_stale_partial(self):
        item = ugc.BatchItem(
            "one",
            "https://example.com/file",
            "one.bin",
            "CC0",
            sha256=hashlib.sha256(b"abcdef").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin.part").write_bytes(b"abc")

            def opener(_request, **_kwargs):
                return FakeResponse(
                    b"def",
                    status=206,
                    headers={"Content-Length": "3", "Content-Range": "bytes 2-4/6"},
                )

            with self.assertRaisesRegex(ugc.DownloadError, "Content-Range"):
                ugc.download_item(
                    item, cache, resume=True, opener=opener, allow_test_opener=True
                )
            self.assertFalse((cache / "one.bin.part").exists())

    def test_declared_and_streamed_size_limits_are_enforced(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def declared(_request, **_kwargs):
                return FakeResponse(b"", headers={"Content-Length": "11"})

            with self.assertRaisesRegex(ugc.DownloadError, "declared download size"):
                ugc.download_item(
                    item,
                    cache,
                    opener=declared,
                    allow_test_opener=True,
                    max_bytes=10,
                )

            def streamed(_request, **_kwargs):
                return FakeResponse(b"12345678901", headers={})

            with self.assertRaisesRegex(ugc.DownloadError, "byte limit"):
                ugc.download_item(
                    item,
                    cache,
                    opener=streamed,
                    allow_test_opener=True,
                    max_bytes=10,
                    chunk_size=4,
                )
            self.assertFalse((cache / "one.bin.part").exists())

    def test_verified_final_removes_stale_part_when_resuming(self):
        payload = b"already complete"
        item = ugc.BatchItem(
            "one",
            "https://example.com/file",
            "one.bin",
            "CC0",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin").write_bytes(payload)
            (cache / "one.bin.part").write_bytes(b"stale")

            def opener(_request, **_kwargs):
                self.fail("network must not be called")

            result = ugc.download_item(
                item, cache, resume=True, opener=opener, allow_test_opener=True
            )
            self.assertEqual("skipped", result.status)
            self.assertFalse((cache / "one.bin.part").exists())

    def test_oversized_verified_cache_cannot_bypass_max_bytes(self):
        payload = b"12345678901"
        item = ugc.BatchItem(
            "one",
            "https://example.com/file",
            "one.bin",
            "CC0",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin").write_bytes(payload)

            def opener(_request, **_kwargs):
                self.fail("network must not be called")

            with self.assertRaisesRegex(ugc.DownloadError, "cached file exceeds"):
                ugc.download_item(
                    item,
                    cache,
                    resume=True,
                    opener=opener,
                    allow_test_opener=True,
                    max_bytes=len(payload) - 1,
                )

    def test_redirect_to_private_address_is_rejected_before_writing(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(_request, **_kwargs):
                return FakeResponse(b"secret", url="http://127.0.0.1/admin")

            with self.assertRaisesRegex(ugc.DownloadError, "unsafe redirect"):
                ugc.download_item(item, cache, opener=opener, allow_test_opener=True)
            self.assertFalse((cache / "one.bin.part").exists())

    def test_hard_linked_partial_is_refused(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            outside = cache / "outside.bin"
            outside.write_bytes(b"do not touch")
            os.link(outside, cache / "one.bin.part")

            def opener(_request, **_kwargs):
                self.fail("network must not be called")

            with self.assertRaisesRegex(ugc.DownloadError, "hard-linked"):
                ugc.download_item(
                    item, cache, resume=True, opener=opener, allow_test_opener=True
                )
            self.assertEqual(b"do not touch", outside.read_bytes())

    def test_part_swap_during_atomic_promotion_is_detected_and_removed(self):
        payload = b"verified payload"
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            real_replace = os.replace

            def opener(_request, **_kwargs):
                return FakeResponse(payload)

            def swapping_replace(source, target):
                Path(source).unlink()
                Path(source).write_bytes(b"attacker payload")
                real_replace(source, target)

            with mock.patch.object(ugc.os, "replace", side_effect=swapping_replace):
                with self.assertRaisesRegex(ugc.DownloadError, "identity changed"):
                    ugc.download_item(item, cache, opener=opener, allow_test_opener=True)
            self.assertFalse((cache / "one.bin").exists())

    def test_range_not_satisfiable_resets_partial(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "one.bin.part").write_bytes(b"stale")

            def opener(request, **_kwargs):
                raise urllib.error.HTTPError(request.full_url, 416, "range", {}, None)

            with self.assertRaisesRegex(ugc.DownloadError, "HTTP 416"):
                ugc.download_item(
                    item, cache, resume=True, opener=opener, allow_test_opener=True
                )
            self.assertFalse((cache / "one.bin.part").exists())

    def test_total_timeout_is_checked_after_each_read(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)

            def opener(_request, **kwargs):
                self.assertEqual(1, kwargs["timeout"])
                return FakeResponse(b"late")

            with mock.patch.object(ugc.time, "monotonic", side_effect=[0.0, 0.1, 2.0]):
                with self.assertRaisesRegex(ugc.DownloadError, "exceeded 1s timeout"):
                    ugc.download_item(
                        item,
                        cache,
                        opener=opener,
                        allow_test_opener=True,
                        timeout=1,
                    )
            self.assertFalse((cache / "one.bin.part").exists())

    def test_timeout_rejects_nan_and_infinity(self):
        item = ugc.BatchItem("one", "https://example.com/file", "one.bin", "CC0")
        with tempfile.TemporaryDirectory() as temporary:
            for timeout in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "timeout"):
                    ugc.download_item(item, Path(temporary), timeout=timeout)

    def test_verified_dns_address_is_the_address_connected(self):
        fake_socket = FakeSocket()
        resolver_calls = []

        def resolver(host, port, **_kwargs):
            resolver_calls.append((host, port))
            return [
                (
                    ugc.socket.AF_INET,
                    ugc.socket.SOCK_STREAM,
                    ugc.socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]

        with mock.patch.object(ugc.socket, "socket", return_value=fake_socket) as socket_factory:
            connected = ugc._connect_public_socket(
                ("example.com", 443),
                1.0,
                None,
                resolver=resolver,
                deadline=ugc.time.monotonic() + 1.0,
            )
        self.assertIs(fake_socket, connected)
        self.assertEqual([("example.com", 443)], resolver_calls)
        self.assertEqual(("93.184.216.34", 443), fake_socket.connected)
        socket_factory.assert_called_once_with(
            ugc.socket.AF_INET, ugc.socket.SOCK_STREAM, ugc.socket.IPPROTO_TCP
        )

    def test_dns_resolution_obeys_download_deadline(self):
        blocker = threading.Event()

        def resolver(_host, _port, **_kwargs):
            blocker.wait(1)
            return []

        started = ugc.time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "resolution timed out"):
            ugc._resolve_with_deadline(
                resolver, "example.com", 443, ugc.time.monotonic() + 0.02
            )
        self.assertLess(ugc.time.monotonic() - started, 0.5)

    def test_batch_keeps_manifest_order_and_surfaces_failure(self):
        items = [
            ugc.BatchItem("disabled", "https://example.com/a", "a.bin", "CC0", enabled=False),
            ugc.BatchItem("broken", "https://example.com/b", "b.bin", "CC0"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            def opener(_request, **_kwargs):
                raise OSError("offline")

            results = ugc.run_batch(
                items,
                Path(temporary),
                jobs=2,
                opener=opener,
                allow_test_opener=True,
            )
            self.assertEqual(["disabled", "broken"], [result.item_id for result in results])
            self.assertEqual(["disabled", "failed"], [result.status for result in results])
            self.assertIn("offline", results[1].error)


class CliTests(unittest.TestCase):
    def test_merge_then_validate_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            output = root / "atoms.jsonl"
            source.write_text(json.dumps(make_record(), ensure_ascii=False) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, ugc.main(["merge", "--input", str(source), "--output", str(output)]))
                self.assertEqual(0, ugc.main(["validate", "--input", str(output)]))

    def test_validate_cli_returns_one_for_schema_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.jsonl"
            path.write_text(json.dumps(make_record(), ensure_ascii=False) + "\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = ugc.main(["validate", "--input", str(path)])
            self.assertEqual(1, code)
            self.assertIn("id must match", stderr.getvalue())

    def test_cli_converts_oserror_to_exit_code_one(self):
        stderr = io.StringIO()
        with mock.patch.object(ugc, "load_many_jsonl", side_effect=OSError("disk unavailable")):
            with contextlib.redirect_stderr(stderr):
                code = ugc.main(["validate", "--input", "missing.jsonl"])
        self.assertEqual(1, code)
        self.assertIn("disk unavailable", stderr.getvalue())

    def test_batch_cli_rejects_non_finite_timeout(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = ugc.main(["batch", "--manifest", "unused.json", "--timeout", "nan"])
        self.assertEqual(1, code)
        self.assertIn("--timeout must be positive", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
