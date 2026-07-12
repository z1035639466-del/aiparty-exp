import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


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
                patch.object(check, "load_whitelist", return_value={}),
                patch.object(check, "load_exclusions", return_value={"old.json": "历史产物"}),
                patch.object(check, "check_file", return_value=[] ) as check_file,
            ):
                self.assertEqual(0, check.main())
            check_file.assert_called_once()
            self.assertEqual("current.json", check_file.call_args.args[0].name)


if __name__ == "__main__":
    unittest.main()
