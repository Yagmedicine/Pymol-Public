"""Regression tests; uses an isolated headless PyMOL instance when available."""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("uniprot_test_plugin", Path(__file__).with_name("__init__.py"))
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)


class Response(io.BytesIO):
    def __init__(self, body, size=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body) if size is None else size)}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def parse(self, contents):
        path = self.root / "list.txt"
        path.write_text(contents, encoding="utf-8-sig")
        return plugin.read_uniprot_file(str(path))

    def test_list_formats(self):
        self.assertEqual(self.parse("# comment\nP06730 IF4E_HUMAN\nP06730\n"), ["P06730", "IF4E_HUMAN"])
        self.assertEqual(self.parse("note,UniProt accession\nx,P06730\ny,P04637\n"), ["P06730", "P04637"])
        self.assertEqual(self.parse("Entry Name\nIF4E_HUMAN\n"), ["IF4E_HUMAN"])
        self.assertEqual(self.parse("accession\tnote\nP06730\tx\n"), ["P06730"])
        self.assertEqual(self.parse("P06730,x\nP04637,y\n"), ["P06730", "P04637"])
        with self.assertRaises(ValueError):
            self.parse("# empty\n")

    def test_atomic_download_and_cache(self):
        path = self.root / "model.pdb"
        with patch.object(plugin.urllib.request, "urlopen", return_value=Response(b"coordinates")):
            plugin._download("https://example.org/model", str(path))
        self.assertEqual(path.read_bytes(), b"coordinates")
        self.assertEqual(list(self.root.glob("*.part")), [])
        with patch.object(plugin.urllib.request, "urlopen", return_value=Response(b"bad", size=20)):
            with self.assertRaises(IOError):
                plugin._download("https://example.org/model", str(path))
        self.assertEqual(path.read_bytes(), b"coordinates")
        self.assertEqual(list(self.root.glob("*.part")), [])
        with patch.object(plugin.urllib.request, "urlopen", side_effect=AssertionError("cache should avoid network")):
            (self.root / "TEST.pdb").write_bytes(b"cached")
            self.assertEqual(plugin._download_pdb_or_cif("TEST", str(self.root)), str(self.root / "TEST.pdb"))

    def test_cancelled_download_leaves_no_cache(self):
        path = self.root / "model.pdb"
        cancel = threading.Event()
        def report(event):
            cancel.set()
        with patch.object(plugin.urllib.request, "urlopen", return_value=Response(b"x" * 600000)):
            with self.assertRaises(plugin.Cancelled):
                plugin._download("https://example.org/model", str(path), progress=report, cancel=cancel)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_progress_distinguishes_cached_downloaded_failed(self):
        refs = [plugin.PDBReference(code, ["A"]) for code in ("OLD1", "NEW1", "BAD1")]
        entry = plugin.UniProtEntry("P06730", "IF4E_HUMAN", refs)
        (self.root / "OLD1.pdb").write_text("old")
        def fetch(code, folder, **kwargs):
            if code == "BAD1":
                raise IOError("missing")
            path = Path(folder) / (code + ".pdb")
            path.write_text("coordinates")
            return str(path)
        events = []
        with patch.object(plugin, "resolve_uniprot", return_value=entry), patch.object(plugin, "_download_pdb_or_cif", side_effect=fetch):
            result = plugin.load_uniprot_structures("P06730", include_alphafold=False,
                download_only=True, cache_dir=str(self.root), progress=events.append)
        self.assertEqual((result["downloaded"], result["cached"], result["failed"]), (1, 1, 1))
        self.assertEqual((events[-1]["completed"], events[-1]["total"]), (3, 3))
        self.assertIn("0 remaining", events[-1]["message"])
        self.assertEqual(result["loaded"], [])

    def test_worker_reports_memory_error(self):
        with patch.object(plugin, "load_uniprot_batch", side_effect=MemoryError):
            thread, cancel, events = plugin._start_job(["P06730"], {})
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        kind, message = events.get_nowait()
        self.assertEqual(kind, "error")
        self.assertIn("Download only", message)

    def test_batch_recovers_and_releases_lock(self):
        summary = {"errors": [], "downloaded": 1, "cached": 0}
        with patch.object(plugin, "_load_uniprot_structures", side_effect=[ValueError("bad entry"), summary]) as load:
            result = plugin.load_uniprot_batch(["bad", "good", "good"])
        self.assertEqual(load.call_count, 2)
        self.assertEqual(len(result["summaries"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(plugin._RUN_LOCK.locked())
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(plugin.Cancelled):
            plugin.load_uniprot_batch(["P06730"], cancel=cancelled)
        self.assertFalse(plugin._RUN_LOCK.locked())


try:
    import pymol2
except ImportError:
    pymol2 = None


@unittest.skipIf(pymol2 is None, "PyMOL not available")
class PyMOLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = pymol2.PyMOL()
        cls.instance.start()
        cls.old_cmd = plugin.cmd
        plugin.cmd = cls.instance.cmd

    @classmethod
    def tearDownClass(cls):
        plugin.cmd = cls.old_cmd
        cls.instance.stop()

    def setUp(self):
        self.cmd = self.instance.cmd
        self.cmd.delete("all")
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = str(Path(self.folder.name) / "full.pdb")
        self.cmd.fab("ACDEFGHIKLMNPQRSTVWYACDEFGHIKLMNPQRSTVWY", "mapped")
        self.cmd.alter("mapped", "chain='B'")
        self.cmd.create("unrelated", "mapped")
        self.cmd.alter("unrelated", "chain='A'")
        self.cmd.translate([30, 0, 0], "unrelated")
        self.cmd.create("full", "mapped or unrelated")
        self.cmd.save(self.path, "full")
        self.cmd.delete("all")

    def test_keeps_mapped_B_instead_of_unrelated_A(self):
        obj = plugin._load_related_chains(self.path, "TEST", ["B"], "group")
        self.assertEqual(self.cmd.get_chains(obj), ["B"])
        self.assertFalse(any(name.endswith("_full") for name in self.cmd.get_names("objects")))

    def test_prefers_A_only_when_mapped(self):
        obj = plugin._load_related_chains(self.path, "TEST", ["A", "B"], "group")
        self.assertEqual(self.cmd.get_chains(obj), ["A"])

    def test_no_matching_mapping_cleans_temporary_object(self):
        with self.assertRaises(ValueError):
            plugin._load_related_chains(self.path, "TEST", ["Z"], "group")
        self.assertEqual(self.cmd.get_object_list(), [])

    def test_preserves_ensemble_states(self):
        self.cmd.load(self.path, "ensemble")
        self.cmd.create("ensemble", "ensemble", source_state=1, target_state=2)
        self.cmd.save(self.path, "ensemble", state=0)
        self.cmd.delete("all")
        obj = plugin._load_related_chains(self.path, "TEST", ["B"], "group")
        self.assertEqual(self.cmd.count_states(obj), 2)
        self.assertEqual(self.cmd.get_chains(obj), ["B"])

    def test_real_alignment_to_reference_chain(self):
        ref = plugin._load_related_chains(self.path, "REF", ["B"], "group")
        mobile = plugin._load_related_chains(self.path, "MOB", ["B"], "group")
        self.cmd.translate([8, -4, 3], mobile)
        for method in ("align", "super", "cealign"):
            plugin._align_object(mobile, ref, method, None)
            self.assertLess(self.cmd.rms_cur(mobile + " and name CA", ref + " and name CA", matchmaker=-1), 0.1)

    def test_fallback_reference_and_failed_fit_hidden(self):
        entry = plugin.UniProtEntry("P06730", "IF4E_HUMAN", [plugin.PDBReference("ONE1", ["B"]), plugin.PDBReference("TWO2", ["B"])])
        with patch.object(plugin, "resolve_uniprot", return_value=entry), patch.object(plugin, "_download_pdb_or_cif", return_value=self.path), patch.object(plugin, "_align_object", side_effect=ValueError("no fit")):
            summary = plugin.load_uniprot_structures("P06730", include_alphafold=False, cache_dir=self.folder.name)
        self.assertEqual(summary["visible"], ["ONE1"])
        self.assertEqual(summary["loaded"], ["ONE1", "TWO2"])
        self.assertNotIn("TWO2", self.cmd.get_names("objects", enabled_only=1))
        self.assertEqual(len(summary["errors"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
