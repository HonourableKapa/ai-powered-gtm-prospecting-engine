import csv
import json
import tempfile
import unittest
from pathlib import Path

from gtm_prospecting.workflow.combine import combine_runs
from gtm_prospecting.workflow.steps import ROW_COLUMNS


def _run(root, name, rows, spend=None):
    d = Path(root) / name
    (d / "export" / "evidence").mkdir(parents=True)
    (d / "outreach_drafts").mkdir()
    with open(d / "export" / "ranked_prospects.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ROW_COLUMNS, restval="")
        w.writeheader()
        for h, tier, score, conf, promo, draft in rows:
            w.writerow({"handle": h, "qualification_tier": tier, "evidence_score": score, "semantic_confidence": conf,
                        "promotional_concentration_semantic": promo, "outreach_draft_status": draft,
                        "expert_category": "onchain_analyst", "audience_tier": "nano(<1k)"})
            (d / "export" / "evidence" / f"{h}.json").write_text("{}", encoding="utf-8")
            if draft == "DRAFT_NOT_SENT":
                (d / "outreach_drafts" / f"{h}.json").write_text("{}", encoding="utf-8")
    if spend is not None:
        (d / "apify_runs.jsonl").write_text(json.dumps({"stage": "discover", "run_id": "r", "usd": spend}) + "\n", encoding="utf-8")
    return d


class TestCombine(unittest.TestCase):
    def test_merge_order_roles_flags_and_no_send(self):
        with tempfile.TemporaryDirectory() as t:
            a = _run(t, "a", [("alice", "A_QUALIFIED", "50", "high", "low", "DRAFT_NOT_SENT"),
                              ("bob", "D_NOT_QUALIFIED", "99", "high", "low", "NO_DRAFT")], spend=0.5)
            b = _run(t, "b", [("Alice", "A_QUALIFIED", "60", "medium", "low", "DRAFT_NOT_SENT"),
                              ("carol", "A_QUALIFIED", "40", "low", "low", "NO_DRAFT"),
                              ("dave", "A_QUALIFIED", "30", "high", "high", "NO_DRAFT")], spend=0.25)
            out = Path(t) / "out"
            rows = combine_runs([a, b], out, primary_n=2)
            self.assertEqual([r["handle"] for r in rows], ["Alice", "carol", "dave"])  # deduped, D excluded, score order
            self.assertEqual([r["role"] for r in rows], ["PRIMARY", "PRIMARY", "ALTERNATE"])
            self.assertEqual([r["needs_spot_check"] for r in rows], [False, True, True])
            self.assertTrue((out / "outreach_drafts" / "Alice.json").exists())
            self.assertFalse((out / "outreach_drafts" / "carol.json").exists())
            text = (out / "combined_summary.md").read_text(encoding="utf-8")
            self.assertIn("Nothing has been sent", text)
            self.assertIn("$0.500", text)
            self.assertIn("$0.250", text)

    def test_missing_export_raises(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(FileNotFoundError):
                combine_runs([Path(t) / "nope"], Path(t) / "out")


if __name__ == "__main__":
    unittest.main()
