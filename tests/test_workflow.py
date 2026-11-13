"""Offline tests for the workflow layer (spec validation, Apify REST client safeguards, token loading). No network, no paid calls, no API key."""
import copy

import csv

import json

import shutil

import socket

import tempfile

import unittest

from datetime import datetime, timezone, timedelta

from pathlib import Path

from types import SimpleNamespace

from unittest import mock

from gtm_prospecting import semantic_llm as S

from gtm_prospecting.workflow import apify_rest as A, cli, spec as SP, steps as ST

ROOT = Path(__file__).resolve().parent.parent

BASE_REQ = {"request_text": "test", "field": "DeFi research", "n": 5, "window_days": 30,
            "keywords": ["DeFi research", "onchain analysis", "smart contract audit"]}

def req(**kw):
    return SP.validate_spec({**BASE_REQ, **kw})

def assessment(payload, rec="REVIEW", strong_url=None):
    url = payload["posts"][0]["url"]
    dim = lambda r, u: {"rating": r, "rationale": "x", "supporting_urls": u}
    a = {"expert_category": "builder_or_developer", "recommendation": rec, "confidence": "medium", "summary": "s",
         "dimensions": {**{d: dim("weak", [url]) for d in S.DIMENSIONS}, "promotional_concentration": dim("low", [])},
         "post_assessments": [{"url": p["url"], "label": "ordinary_original_post", "reason": "r"} for p in payload["posts"]],
         "bio_claims": [], "supporting_evidence": [{"url": url, "point": "p"}], "conflicting_evidence": [],
         "missing_verification": ["repo ownership"], "external_checks": []}
    if strong_url:
        a["dimensions"]["original_research_or_development"] = dim("strong", [strong_url])
    return a

class FakeApify:
    def __init__(self, usd=0.0123, items=None):
        self.runs, self.usd, self.items = [], usd, items or []

    def actor_price_per_1000(self):
        return 0.25, "fake"

    def run(self, actor_input, cap):
        self.runs.append((actor_input, cap))
        run = {"id": "RUN1", "status": "SUCCEEDED", "defaultDatasetId": "DS1"}
        if self.usd is not None:
            run["usageTotalUsd"] = self.usd
        return run, self.items

class TestSpec(unittest.TestCase):
    def test_defaults_and_no_invented_audience_threshold(self):
        s = req()
        self.assertEqual((s["n"], s["window_days"], s["language"]), (5, 30, "en"))
        self.assertIsNone(s["audience"])            # no follower minimum unless the user states one
        self.assertIsNone(s["region"])

    def test_explicit_audience_and_region(self):
        s = req(audience={"min_followers": 5000}, region={"terms": ["Singapore"], "required": True})
        self.assertEqual(s["audience"], {"min_followers": 5000})
        self.assertEqual(s["region"]["terms"], ["singapore"])

    def test_validation_errors(self):
        for bad in ({"field": ""}, {"keywords": []}, {"n": 0}, {"n": 500}, {"window_days": 0}, {"bogus": 1},
                    {"audience": {"min_likes": 5}}, {"audience": {"min_followers": -1}}, {"region": {"terms": []}},
                    {"keywords": ["k"] * 21}):
            with self.assertRaises(SP.SpecError, msg=str(bad)):
                req(**bad)

class TestApifyRest(unittest.TestCase):
    def test_inputs_use_window_and_exclude_noise(self):
        s = req(window_days=30)
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        d = A.discovery_input(s, ST.build_queries(s), now)
        self.assertEqual(d["startDate"], "2026-10-14")
        self.assertFalse(d["includeReplies"])
        self.assertEqual(d["maxResults"], 50 * 3)
        p = A.profile_input(s, ["a", "b"])
        self.assertEqual((p["profiles"], p["maxResultsPerInput"], p["maxResults"]), (["a", "b"], 15, 30))
        self.assertEqual(ST.build_queries(req(keywords=["DeFi research", "defi research", "TVL"])), ['"DeFi research"', "TVL"])

    def test_estimate_and_approval(self):
        est = A.estimate({"maxResults": 1000}, 2.0)
        self.assertEqual(est["estimated_max_usd"], 2.0)
        with self.assertRaises(A.ApprovalRequired):
            A.require_approval(False, 5, est)
        with self.assertRaises(A.ApprovalRequired):
            A.require_approval(True, None, est)
        with self.assertRaises(A.ApprovalRequired):
            A.require_approval(True, 1.0, est)          # cap below the estimated upper bound
        A.require_approval(True, 2.0, est)

    def test_token_only_from_env_and_never_in_messages(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(A, "DEFAULT_ENV_FILE", Path(tempfile.gettempdir()) / "definitely-no-such.env"):
            with self.assertRaises(A.ApifyAuthMissing) as c:
                A.get_token()
        self.assertNotIn("apify_api_", str(c.exception))

    def test_run_sends_server_side_cap_and_bearer_header_not_in_url(self):
        calls = []

        def http(method, url, headers, body):
            calls.append((method, url, headers, body))
            if method == "POST":
                return 201, {"data": {"id": "R", "status": "RUNNING", "defaultDatasetId": "D"}}
            if "/actor-runs/" in url:
                return 200, {"data": {"id": "R", "status": "SUCCEEDED", "defaultDatasetId": "D", "usageTotalUsd": 0.01}}
            return 200, [{"tweet_id": "1"}]
        run, items = A.Apify(token="SECRET-TOKEN-123", http=http).run({"searchTerms": ["x"], "maxResults": 5}, 0.5)
        self.assertEqual(items, [{"tweet_id": "1"}])
        post = calls[0]
        self.assertIn("maxTotalChargeUsd=0.5", post[1])
        self.assertEqual(post[2]["Authorization"], "Bearer SECRET-TOKEN-123")
        self.assertTrue(all("SECRET-TOKEN-123" not in c[1] for c in calls))

    def test_http_error_message_has_no_token(self):
        api = A.Apify(token="SECRET-TOKEN-123", http=lambda *a: (401, {"error": {"message": "bad token"}}))
        with self.assertRaises(RuntimeError) as c:
            api.run({"maxResults": 1}, 1)
        self.assertNotIn("SECRET-TOKEN-123", str(c.exception))

    def test_price_uses_the_accounts_plan_tier(self):
        tiers = {t: {"tieredEventPriceUsd": v} for t, v in
                 (("FREE", 0.002), ("BRONZE", 0.0002), ("SILVER", 0.0002), ("GOLD", 0.0002))}
        info = {"data": {"pricingInfos": [{"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
            "tweet-result": {"eventTieredPricingUsd": tiers}}}}]}}

        def api_for(plan):
            def http(method, url, headers, body):
                if url.endswith("/users/me"):
                    return 200, {"data": {"plan": {"id": plan}}} if plan else (200, {"data": {}})
                return 200, info
            return A.Apify(token="t", http=http)
        p_free, src = api_for("FREE").actor_price_per_1000()
        p_bronze, _ = api_for("BRONZE").actor_price_per_1000()
        p_unknown, src_unknown = api_for(None).actor_price_per_1000()
        self.assertEqual((p_free, p_bronze), (2.0, 0.2))
        self.assertIn("FREE", src)
        self.assertEqual(p_unknown, 2.0)                       # unknown plan: pessimistic highest tier
        self.assertIn("highest tier assumed", src_unknown)

    def test_get_requests_are_retried_but_post_is_never_retried(self):
        gets, posts = [], []

        def http(method, url, headers, body):
            if method == "GET":
                gets.append(url)
                if len(gets) < 3:
                    raise TimeoutError("read timed out")
                return 200, {"data": {"ok": True}}
            posts.append(url)
            raise TimeoutError("read timed out")
        api = A.Apify(token="t", http=http)
        self.assertEqual(api._call("GET", "/users/me"), {"data": {"ok": True}})     # free read: retried
        self.assertEqual(len(gets), 3)
        with self.assertRaises(TimeoutError):
            api._call("POST", "/acts/x/runs", None, {})
        self.assertEqual(len(posts), 1)                                          # paid action: exactly one attempt

    def test_final_usage_is_reread_after_completion(self):
        def http(method, url, headers, body):
            if method == "POST":
                return 201, {"data": {"id": "R", "status": "SUCCEEDED", "defaultDatasetId": "D", "usageTotalUsd": 0.272}}
            if "/datasets/" in url:
                return 200, [{"tweet_id": "1"}]
            return 200, {"data": {"id": "R", "status": "SUCCEEDED", "defaultDatasetId": "D", "usageTotalUsd": 0.358}}
        run, items = A.Apify(token="t", http=http).run({"maxResults": 1}, 1.0)
        self.assertEqual(run["usageTotalUsd"], 0.358)        # not the provisional figure

    def test_interrupted_run_carries_run_id_and_is_logged_as_unknown_billing(self):
        def http(method, url, headers, body):
            if method == "POST":
                return 201, {"data": {"id": "RUN-X", "status": "RUNNING", "defaultDatasetId": "D"}}
            raise TimeoutError("read timed out")
        with self.assertRaises(A.ApifyRunInterrupted) as c:
            A.Apify(token="t", http=http).run({"maxResults": 1}, 1.0)
        self.assertEqual(c.exception.run_id, "RUN-X")
        self.assertIn("do not start a new run", str(c.exception))

    def test_cli_logs_interrupted_run_as_unknown_billing_and_stops(self):
        class Dropped(FakeApify):
            def run(self, actor_input, cap):
                raise A.ApifyRunInterrupted("RUN-Y", "TimeoutError")
        d = SP.new_run_dir(Path(tempfile.mkdtemp()), req(), "t")
        self.addCleanup(shutil.rmtree, d.parent, ignore_errors=True)
        rc = cli.main(["discover", "--run", str(d), "--approve", "--max-usd", "5"], client_factory=lambda: Dropped())
        self.assertEqual(rc, 2)
        rec = json.loads((d / "apify_runs.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((rec["run_id"], rec["billing_status"], rec["usd"]), ("RUN-Y", "unknown", None))

    def test_price_discovery_with_labelled_fallback(self):
        info = {"data": {"pricingInfos": [{"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
            "tweet-result": {"eventPriceUsd": 0.0002}}}}]}}
        p, src = A.Apify(token="t", http=lambda *a: (200, info)).actor_price_per_1000()
        self.assertEqual(p, 0.2)
        p2, src2 = A.Apify(token="t", http=lambda *a: (500, None)).actor_price_per_1000()
        self.assertEqual(p2, A.ASSUMED_PRICE_PER_1000)
        self.assertIn("assumed", src2)

class TestEnvFileToken(unittest.TestCase):
    """Uses only fake values in temp files. The project's real .env is never read by these tests."""
    FAKE = "fake-token-for-tests-123"

    def env_file(self, text):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = d / ".env"
        p.write_text(text, encoding="utf-8")
        return p

    def test_token_loaded_from_env_file(self):
        p = self.env_file(f"APIFY_TOKEN={self.FAKE}\n")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(A.get_token(env_file=p), self.FAKE)

    def test_quotes_comments_and_spaces_handled(self):
        p = self.env_file(f"# comment\nOTHER=1\nAPIFY_TOKEN = \"{self.FAKE}\"\n")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(A.get_token(env_file=p), self.FAKE)

    def test_real_environment_variable_takes_precedence_over_env_file(self):
        p = self.env_file("APIFY_TOKEN=from-dotenv-file\n")
        with mock.patch.dict("os.environ", {"APIFY_TOKEN": "from-real-environment"}, clear=True):
            self.assertEqual(A.get_token(env_file=p), "from-real-environment")

    def test_loading_does_not_leak_into_process_environment_or_output(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        p = self.env_file(f"APIFY_TOKEN={self.FAKE}\n")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True):
            with redirect_stdout(out), redirect_stderr(err):
                A.get_token(env_file=p)
            self.assertNotIn("APIFY_TOKEN", __import__("os").environ)       # not copied into os.environ
        self.assertNotIn(self.FAKE, out.getvalue() + err.getvalue())

    def test_missing_or_empty_token_raises_without_exposing_anything(self):
        for text in ("", "APIFY_TOKEN=\n", "APIFY_TOKEN=   \n", "OTHER_KEY=" + self.FAKE + "\n"):
            p = self.env_file(text)
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(A.ApifyAuthMissing) as c:
                    A.get_token(env_file=p)
            self.assertNotIn(self.FAKE, str(c.exception))
            self.assertIn(".env.example", str(c.exception))
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(A.ApifyAuthMissing):
                A.get_token(env_file=Path(tempfile.gettempdir()) / "no-such-file.env")

    def test_missing_python_dotenv_is_reported_clearly(self):
        p = self.env_file(f"APIFY_TOKEN={self.FAKE}\n")
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.dict("sys.modules", {"dotenv": None}):
            with self.assertRaises(A.ApifyAuthMissing) as c:
                A.get_token(env_file=p)
        self.assertIn("python-dotenv", str(c.exception))
        self.assertNotIn(self.FAKE, str(c.exception))

    def test_client_uses_env_file_token_lazily_and_only_in_the_auth_header(self):
        p = self.env_file(f"APIFY_TOKEN={self.FAKE}\n")
        seen = []

        def http(method, url, headers, body):
            seen.append((url, headers))
            return 200, {"data": {"pricingInfos": []}}
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(A, "DEFAULT_ENV_FILE", p):
            api = A.Apify(http=http)                        # no token passed: resolved from .env on first call
            self.assertEqual(seen, [])                      # nothing read or sent at construction
            api.actor_price_per_1000()
        self.assertEqual(seen[0][1]["Authorization"], f"Bearer {self.FAKE}")
        self.assertNotIn(self.FAKE, seen[0][0])             # never in the URL

    def test_project_hygiene_gitignore_example_and_requirements(self):
        lines = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        for rule in (".env", ".env.*", "!.env.example"):
            self.assertIn(rule, lines)
        self.assertLess(lines.index(".env.*"), lines.index("!.env.example"))   # negation must come after the ignore
        self.assertEqual((ROOT / ".env.example").read_text(encoding="utf-8").strip(), "APIFY_TOKEN=")
        self.assertIn("python-dotenv", (ROOT / "requirements.txt").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
