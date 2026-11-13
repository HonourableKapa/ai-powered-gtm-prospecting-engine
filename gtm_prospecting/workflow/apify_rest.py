"""Apify REST client for coregent/x-tweet-scraper (stdlib only). Paid runs need an explicit approval.

Why REST and not only MCP: bulk dataset rows would flood the chat context through MCP tools, and scripts
cannot reuse an OAuth connection. The token comes from the APIFY_TOKEN environment variable or, failing
that, the project's git-ignored .env file, and is never printed, logged or written anywhere.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ACTOR = "coregent~x-tweet-scraper"
BASE = "https://api.apify.com/v2"
# Used only until the account's real price is fetched. Deliberately pessimistic: the Store lists
# "from $0.20 / 1,000 tweet results" at the best plan tier; free/lower tiers cost more.
ASSUMED_PRICE_PER_1000 = 2.00


class ApifyAuthMissing(RuntimeError):
    pass


class ApprovalRequired(RuntimeError):
    pass


class ApifyRunInterrupted(RuntimeError):
    """The paid run was started but the connection failed before results were saved. The run may have
    completed and been billed: billing status is UNKNOWN until checked in Apify Console."""
    def __init__(self, run_id, cause):
        self.run_id = run_id
        super().__init__(f"Apify run {run_id} was started but the connection failed ({cause}). It may have finished and "
                         "been billed. Check Apify Console, then recover the results from that run's dataset; do not start a new run.")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def get_token(env_file=None):
    """APIFY_TOKEN from the real environment first, else from the project's .env (python-dotenv).
    Real environment variables take precedence. The value is returned to the caller only: it is never
    printed, logged, put in an exception message, or copied into os.environ."""
    t = os.environ.get("APIFY_TOKEN", "").strip()
    if t:
        return t
    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    dotenv_missing = False
    if path.is_file():
        try:
            from dotenv import dotenv_values
            t = (dotenv_values(path).get("APIFY_TOKEN") or "").strip()
        except ImportError:
            dotenv_missing = True
    if not t:
        why = ("python-dotenv is not installed (pip install -r requirements.txt)" if dotenv_missing
               else "found neither an APIFY_TOKEN environment variable nor a non-empty APIFY_TOKEN= line in the project's .env")
        raise ApifyAuthMissing(f"APIFY_TOKEN not available: {why}. Create a token in Apify Console > Settings > "
                               "API & Integrations and put it in .env (see .env.example). Never paste it into chat.")
    return t


def default_http(method, url, headers, body):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


# ------------------------------------------------------------------ inputs and cost estimates (no network)
def discovery_input(spec, queries, today=None):
    today = today or datetime.now(timezone.utc)
    since = (today - timedelta(days=spec["window_days"])).strftime("%Y-%m-%d")
    return {"searchTerms": queries, "maxResultsPerInput": spec["discovery_per_query"],
            "maxResults": spec["discovery_per_query"] * len(queries), "startDate": since,
            "language": spec["language"], "includeReplies": False, "includeRetweets": False}


def profile_input(spec, handles):
    return {"profiles": handles, "maxResultsPerInput": spec["profile_posts"],
            "maxResults": spec["profile_posts"] * len(handles), "includeReplies": False, "includeRetweets": False}


def max_tweets(actor_input) -> int:
    return int(actor_input["maxResults"])


def estimate(actor_input, price_per_1000=ASSUMED_PRICE_PER_1000, price_source="assumed (unverified, pessimistic)"):
    n = max_tweets(actor_input)
    return {"max_tweets": n, "price_per_1000_usd": price_per_1000, "price_source": price_source,
            "estimated_max_usd": round(n * price_per_1000 / 1000, 4),
            "note": "Upper bound: you are charged per unique tweet saved, and usually fewer are returned."}


def _find_price(node, tier=None):
    """Best-effort: locate the 'tweet-result' event price inside actor pricing info. If the account's plan
    tier is known and priced, use it; otherwise fall back to the highest tier (pessimistic)."""
    if isinstance(node, dict):
        if "tweet-result" in node:
            return _find_price(node["tweet-result"], tier)
        if isinstance(node.get("eventPriceUsd"), (int, float)):
            return float(node["eventPriceUsd"])
        tiers = node.get("eventTieredPricingUsd")
        if isinstance(tiers, dict):
            mine = tiers.get(tier) if tier else None
            if isinstance(mine, dict) and isinstance(mine.get("tieredEventPriceUsd"), (int, float)):
                return float(mine["tieredEventPriceUsd"])
            vals = [v.get("tieredEventPriceUsd") for v in tiers.values() if isinstance(v, dict)]
            vals = [v for v in vals if isinstance(v, (int, float))]
            if vals:
                return float(max(vals))
        for v in node.values():
            r = _find_price(v, tier)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_price(v, tier)
            if r is not None:
                return r
    return None


class Apify:
    def __init__(self, token=None, http=default_http):
        self._token, self.http = token, http

    @property
    def token(self):
        if self._token is None:
            self._token = get_token()
        return self._token

    def _call(self, method, path, query=None, body=None):
        url = f"{BASE}{path}" + (("?" + urllib.parse.urlencode(query)) if query else "")
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        payload = json.dumps(body).encode() if body is not None else None
        attempts = 3 if method == "GET" else 1      # only free, idempotent reads are retried; a POST would start another paid run
        for i in range(attempts):
            try:
                st, data = self.http(method, url, headers, payload)
                break
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                if i == attempts - 1:
                    raise
        if st >= 400:
            msg = (data or {}).get("error", {}).get("message") if isinstance(data, dict) else None
            raise RuntimeError(f"Apify API {method} {path} -> HTTP {st}: {msg or 'no details'}")
        return data

    def actor_price_per_1000(self):
        """Read-only call (free). Returns (price_per_1000_usd, source) with a labelled fallback."""
        try:
            tier = None
            try:
                plan = ((self._call("GET", "/users/me") or {}).get("data", {}).get("plan") or {}).get("id")
                tier = str(plan).upper() if plan else None
            except Exception:
                pass                                  # unknown plan: pessimistic highest tier is used
            info = self._call("GET", f"/acts/{ACTOR}")
            p = _find_price((info or {}).get("data", {}).get("pricingInfos"), tier)
            if p is not None:
                return round(p * 1000, 4), f"fetched from Apify actor pricing info (plan tier: {tier or 'unknown, highest tier assumed'})"
        except Exception:
            pass
        return ASSUMED_PRICE_PER_1000, "assumed (unverified, pessimistic): could not read actor pricing"

    def run(self, actor_input, max_total_charge_usd, timeout_secs=600, max_polls=20):
        """Start a run with a SERVER-ENFORCED cost cap, wait for it, return (run, items). Caller must have approval."""
        q = {"maxTotalChargeUsd": max_total_charge_usd, "timeout": timeout_secs, "waitForFinish": 60}
        run = self._call("POST", f"/acts/{ACTOR}/runs", q, actor_input)["data"]
        try:
            polls = 0
            while run.get("status") in ("READY", "RUNNING", "TIMING-OUT", "ABORTING") and polls < max_polls:
                run = self._call("GET", f"/actor-runs/{run['id']}", {"waitForFinish": 60})["data"]
                polls += 1
            items = []
            if run.get("status") in ("SUCCEEDED", "TIMED-OUT", "ABORTED") and run.get("defaultDatasetId"):
                offset = 0
                while True:
                    page = self._call("GET", f"/datasets/{run['defaultDatasetId']}/items",
                                      {"format": "json", "clean": "true", "limit": 1000, "offset": offset})
                    items += page or []
                    if not page or len(page) < 1000:
                        break
                    offset += 1000
            try:  # usage on the run object can still be provisional at completion: re-read it now that time has passed
                run = {**run, **self._call("GET", f"/actor-runs/{run['id']}")["data"]}
            except Exception:
                pass
        except Exception as e:
            raise ApifyRunInterrupted(run.get("id"), type(e).__name__) from e
        return run, items


def require_approval(approve: bool, max_usd, est):
    """Paid runs need --approve AND an explicit --max-usd that covers the estimated upper bound."""
    if not approve:
        raise ApprovalRequired("paid Apify run needs explicit approval (--approve)")
    if max_usd is None or max_usd <= 0:
        raise ApprovalRequired("paid Apify run needs an explicit --max-usd cap")
    if est["estimated_max_usd"] > max_usd:
        raise ApprovalRequired(f"estimated upper bound ${est['estimated_max_usd']:.2f} exceeds --max-usd ${max_usd:.2f}; "
                               "raise the cap or reduce the request")
