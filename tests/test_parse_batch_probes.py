import json
import tempfile
import unittest
from pathlib import Path

import run_ugc_collection as ugc
from research.ugc import parse_batch_probes as batch


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "research/ugc/batch_sources.json"


def synthetic_snapshots(sources):
    marker_text = {str(source["id"]): [] for source in sources}
    for recipe in batch.RECIPES:
        for source_id, markers in recipe.source_markers.items():
            marker_text[source_id].extend(markers)
    return {
        source_id: batch.Snapshot(
            source_id=source_id,
            visible_text=batch._search_text(" ".join(markers)),
            headings=(),
            byte_count=1,
        )
        for source_id, markers in marker_text.items()
        if markers
    }


class VisibleHTMLTests(unittest.TestCase):
    def test_parser_keeps_visible_text_and_drops_scripts(self):
        payload = (
            b"<html><head><script>secret marker</script></head><body>"
            b"<h2>Visible Heading</h2><p>Useful&nbsp;mechanic</p></body></html>"
        )
        snapshot = batch.parse_visible_html("probe", payload)
        self.assertIn("visible heading", snapshot.visible_text)
        self.assertIn("useful mechanic", snapshot.visible_text)
        self.assertNotIn("secret marker", snapshot.visible_text)
        self.assertEqual(("visible heading",), snapshot.headings)

    def test_parser_rejects_empty_or_headingless_html(self):
        with self.assertRaisesRegex(batch.BatchParseError, "empty HTML"):
            batch.parse_visible_html("probe", b"")
        with self.assertRaisesRegex(batch.BatchParseError, "headings"):
            batch.parse_visible_html("probe", b"<html><body><p>text only</p></body></html>")

    def test_marker_guard_fails_closed(self):
        snapshot = batch.Snapshot("one", "known phrase", (), 1)
        with self.assertRaisesRegex(batch.BatchParseError, "missing evidence marker"):
            batch._require_markers(snapshot, ("different phrase",), "recipe")


class RecipeCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = batch.load_manifest(MANIFEST)

    def test_catalog_covers_every_enabled_content_probe(self):
        batch.validate_recipe_catalog(self.sources)
        enabled_content = {
            source["id"] for source in self.sources if source["kind"] == batch.CONTENT_KIND
        }
        configured = {
            source_id for recipe in batch.RECIPES for source_id in recipe.source_markers
        }
        self.assertEqual(enabled_content, configured)
        self.assertEqual(11, len(enabled_content))

    def test_all_recipe_records_validate_and_deduplicate(self):
        snapshots = synthetic_snapshots(self.sources)
        candidates = batch.build_candidates(self.sources, snapshots)
        atoms = ugc.merge_records(candidates)
        ugc.validate_located_records([ugc.LocatedRecord(atom) for atom in atoms])

        self.assertEqual(75, len(candidates))
        self.assertEqual(29, len(atoms))
        self.assertEqual(46, len(candidates) - len(atoms))
        self.assertTrue(all(record["track"] == "batch" for record in atoms))
        self.assertTrue(
            all(not (set(record) & ugc.RAW_CONTENT_FIELDS) for record in atoms)
        )

        covered = {
            source_id
            for atom in atoms
            for source_id in batch._source_ids_for_atom(atom)
        }
        self.assertEqual(set(batch.SOURCE_PROFILES), covered)

    def test_end_to_end_with_bounded_synthetic_cache(self):
        snapshots = synthetic_snapshots(self.sources)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            for source in self.sources:
                source_id = source["id"]
                destination = cache / source["filename"]
                if source["kind"] == batch.CONTENT_KIND:
                    markers = snapshots[source_id].visible_text
                    destination.write_text(
                        f"<html><body><h1>{source_id}</h1><p>{markers}</p></body></html>",
                        encoding="utf-8",
                    )
                elif destination.suffix == ".json":
                    destination.write_text(json.dumps({"ok": True}), encoding="utf-8")
                else:
                    destination.write_text("bounded metadata probe", encoding="utf-8")

            atoms_path = root / "batch_atoms.jsonl"
            json_report = root / "batch_parse.json"
            markdown_report = root / "batch_parse.md"
            report = batch.run_parse(
                MANIFEST, cache, atoms_path, json_report, markdown_report
            )

            self.assertEqual(17, report["summary"]["downloaded_probe_count"])
            self.assertEqual(11, report["summary"]["content_source_count"])
            self.assertEqual(6, report["summary"]["metadata_only_source_count"])
            self.assertEqual(29, report["summary"]["deduplicated_atom_count"])
            loaded = ugc.load_jsonl(atoms_path)
            ugc.validate_located_records(loaded)
            self.assertEqual(29, len(loaded))
            self.assertIn("六个语料入口探针没有正文", markdown_report.read_text(encoding="utf-8"))
            self.assertEqual(1, json.loads(json_report.read_text(encoding="utf-8"))["schema_version"])


if __name__ == "__main__":
    unittest.main()
