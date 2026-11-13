"""End-to-end smoke test of the deterministic pipeline on the SYNTHETIC demo data (fictional accounts only).
Also guards repository hygiene: no credential-shaped strings and no private-project terms in published files."""
import csv
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from gtm_prospecting.pipeline import run

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "examples" / "synthetic_data" / "raw"
EVID = ROOT / "config" / "manual_evidence.example.json"


class TestSyntheticPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.res = run(RAW, EVID, cls.tmp / "out")
        cls.by = {r["account"]["username"]: r for r in cls.res["results"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_counts_and_dedupe(self):
        s = self.res["stats"]
        self.assertEqual((s["raw_rows"], s["unique_posts"], s["unique_accounts"]), (32, 22, 5))
        self.assertTrue(all(h.startswith("synth_") for h in self.by))

    def test_rule_layer_never_says_yes_and_never_marks_outreach_eligible(self):
        for r in self.res["results"]:
            self.assertNotEqual(r["decision"]["expert_status"], "YES")
            self.assertFalse(r["decision"]["outreach_eligible"])

    def test_promotional_account_scores_above_research_account_on_promo_risk(self):
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        promo, research = (self.by[h]["signals"]["promo_risk"] for h in ("synth_yieldmax", "synth_chainlens"))
        self.assertGreater(order[promo], order[research])

    def test_report_written(self):
        rows = list(csv.DictReader(open(self.tmp / "out" / "report.csv", encoding="utf-8-sig")))
        self.assertEqual(len(rows), 5)


class TestPublishedExamples(unittest.TestCase):
    def test_example_output_is_labeled_synthetic_and_unsent(self):
        rows = list(csv.DictReader(open(ROOT / "examples" / "output" / "synthetic_example_prospects.csv", encoding="utf-8-sig")))
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertIn("synthetic", r["Name"].lower())
            self.assertIn("SYNTHETIC EXAMPLE", r["Qualification reason"])
            self.assertIn(r["Outreach status"], ("DRAFT_NOT_SENT", "ON_HOLD_LOW_CONFIDENCE (no draft; nothing sent)"))


def _published_text_files():
    suffixes = {".py", ".md", ".json", ".csv", ".txt", ".example"}
    return [p for p in ROOT.rglob("*") if p.is_file() and (p.suffix in suffixes or p.name in {".gitignore", ".env.example"})
            and not {"__pycache__", ".git"} & set(p.parts) and p.name != "test_synthetic_demo.py"]


class TestRepositoryHygiene(unittest.TestCase):
    SECRET = re.compile(r"apify_api_[A-Za-z0-9]{10,}|sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|Bearer [A-Za-z0-9._-]{20,}")
    PRIVATE = re.compile("|".join(["euler" + "phi", "patr" + "ick", "inter" + "view", "take" + "-home", r"C:\\Users", "gno" + "wbe"]), re.I)

    def test_no_credential_shaped_strings(self):
        for p in _published_text_files():
            self.assertIsNone(self.SECRET.search(p.read_text(encoding="utf-8", errors="ignore")), p)

    def test_no_private_project_terms_or_local_paths(self):
        for p in _published_text_files():
            self.assertIsNone(self.PRIVATE.search(p.read_text(encoding="utf-8", errors="ignore")), p)

    def test_env_example_is_placeholder_only(self):
        self.assertEqual((ROOT / ".env.example").read_text(encoding="utf-8").strip(), "APIFY_TOKEN=")
        self.assertFalse((ROOT / ".env").exists())


if __name__ == "__main__":
    unittest.main()
