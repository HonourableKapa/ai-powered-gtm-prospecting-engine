"""Unit tests for the analysis-evidence signal used in discovery triage. Pure functions; no data files, no network."""
import json

import shutil

import tempfile

import unittest

from pathlib import Path

from gtm_prospecting.load import parse_dt

from gtm_prospecting.pipeline import run

from gtm_prospecting.signals import analysis_evidence

from gtm_prospecting.workflow import spec as SP, steps as ST

ROOT = Path(__file__).resolve().parent.parent

def styled(s):
    """Render ASCII capitals as Unicode 'mathematical sans-serif bold' (the lettering templated posts use)."""
    return "".join(chr(0x1D5D4 + ord(c) - 65) if "A" <= c <= "Z" else c for c in s)

class TestAnalysisEvidence(unittest.TestCase):
    RESEARCH = ("I traced the example vault's transfers: $1,250 to one wallet and about $8,400 across 5 transactions. "
                "I compared them against the public dashboard and the median hold was 3 minutes.")

    def test_research_shaped_post_scores_high(self):
        e = analysis_evidence(self.RESEARCH)
        self.assertGreaterEqual(e["score"], 4)
        for k in ("quantitative", "method", "sourced", "investigation", "first_person"):
            self.assertIn(k, e["positive"])
        self.assertEqual(e["negative"], [])

    def test_keyword_stuffing_earns_nothing(self):
        # the old score rewarded exactly this: many technical keywords, no work shown
        e = analysis_evidence("tokenomics audit exploit tvl apy oracle liquidity vesting unlocks emissions")
        self.assertLessEqual(e["score"], 0.5)
        self.assertEqual(analysis_evidence("")["score"], 0.0)

    def test_templated_promotion_shapes_are_penalised(self):
        e = analysis_evidence(styled("EXAMPLECHAIN TOKENOMICS WHERE UTILITY SECURITY AND GOVERNANCE CONNECT") + " Tokenomics becomes more meaningful when we look at supply.")
        self.assertIn("styled_lettering", e["negative"])
        self.assertLess(e["score"], 1)
        e = analysis_evidence("Happy Friday friends.. Automation can already do the work. @example_tool handles one job.")
        self.assertIn("greeting_template", e["negative"])
        e = analysis_evidence("Day 9 | Class recap: Intro to Lending, Research Basics, Wallets, Token Supply")
        self.assertIn("digest_or_course", e["negative"])
        e = analysis_evidence("our analytics tool automatically found $450,000 sitting in a pool. Try it free.")
        self.assertIn("product_voice", e["negative"])
        e = analysis_evidence("EXAMPLE TOKEN BURN UPDATE: COMPLETED THE SECOND SCHEDULED BUYBACK")
        self.assertIn("caps_headline", e["negative"])

    def test_third_person_relay_is_penalised_but_first_person_analysis_of_the_same_data_is_not(self):
        relay = analysis_evidence("ExampleData analysed 5.1M launches and reported that bots made $12.4M, according to the report.")
        self.assertIn("relay", relay["negative"])
        own = analysis_evidence("I analysed 5.1M launches from the public dashboard and bots made $12.4M, my math says 4% of the total.")
        self.assertNotIn("relay", own["negative"])
        self.assertGreater(own["score"], relay["score"])

if __name__ == "__main__":
    unittest.main()
