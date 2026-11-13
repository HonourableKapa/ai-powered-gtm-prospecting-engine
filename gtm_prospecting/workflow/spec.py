"""Validated request spec. Claude Code turns the user's sentence into this JSON; code only validates it.

No audience threshold is ever defaulted: follower/view criteria exist only if the user stated them.
"""
from __future__ import annotations
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

ALLOWED = {"request_text", "field", "n", "window_days", "region", "audience", "keywords", "language",
           "triage_multiplier", "profile_posts", "discovery_per_query", "cache_ttl_days", "draft_for_review_tier",
           "exclude_terms", "exclude_handles"}
AUDIENCE_KEYS = {"min_followers", "max_followers", "min_median_views"}


class SpecError(ValueError):
    pass


def _int(d, k, default, lo, hi):
    v = d.get(k, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise SpecError(f"{k} must be an integer in [{lo}, {hi}], got {v!r}")
    return v


def validate_spec(d: dict) -> dict:
    unknown = set(d) - ALLOWED
    if unknown:
        raise SpecError(f"unknown spec keys: {sorted(unknown)}")
    field = d.get("field")
    if not isinstance(field, str) or not field.strip():
        raise SpecError("field (crypto specialty) is required, e.g. 'DeFi research'")
    kws = d.get("keywords")
    if not isinstance(kws, list) or not kws or not all(isinstance(k, str) and k.strip() for k in kws):
        raise SpecError("keywords must be a non-empty list of search strings")
    if len(kws) > 20:
        raise SpecError("at most 20 keywords per request (cost control)")
    region = d.get("region")
    if region is not None:
        if not isinstance(region, dict) or not region.get("terms"):
            raise SpecError("region must be {'terms': [...], 'required': bool} or null")
        region = {"terms": [str(t).lower() for t in region["terms"]], "required": bool(region.get("required", False)),
                  "note": "matched softly against the self-reported profile location; unknown location is kept and flagged"}
    audience = d.get("audience")
    if audience is not None:
        bad = set(audience) - AUDIENCE_KEYS
        if bad or not audience:
            raise SpecError(f"audience may only contain {sorted(AUDIENCE_KEYS)}")
        for k, v in audience.items():
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise SpecError(f"audience.{k} must be a non-negative integer")
    def _list(key, rx=None):
        v = d.get(key, [])
        if not isinstance(v, list) or len(v) > 40 or not all(isinstance(x, str) and x.strip() and len(x) <= 40 for x in v):
            raise SpecError(f"{key} must be a list of at most 40 short strings")
        if rx and not all(re.fullmatch(rx, x.lstrip("@")) for x in v):
            raise SpecError(f"{key} contains an invalid entry")
        return [x.strip().lstrip("@") if rx else x.strip() for x in v]

    return {
        "exclude_terms": _list("exclude_terms"),                    # words to drop from every search (X '-term' operator)
        "exclude_handles": _list("exclude_handles", r"\w{1,15}"),    # accounts to drop from every search ('-from:handle')
        "request_text": d.get("request_text", ""),
        "field": field.strip(),
        "n": _int(d, "n", 30, 1, 200),
        "window_days": _int(d, "window_days", 30, 1, 365),
        "region": region,
        "audience": audience,                     # None unless the user explicitly asked for criteria
        "keywords": [k.strip() for k in kws],
        "language": d.get("language", "en"),
        "triage_multiplier": _int(d, "triage_multiplier", 3, 1, 10),
        "profile_posts": _int(d, "profile_posts", 15, 5, 50),
        "discovery_per_query": _int(d, "discovery_per_query", 50, 1, 200),     # 1 allows a ~$0.02 syntax probe
        "cache_ttl_days": _int(d, "cache_ttl_days", 14, 0, 365),
        "draft_for_review_tier": bool(d.get("draft_for_review_tier", False)),
    }


def new_run_dir(base, spec, run_id=None):
    slug = re.sub(r"[^a-z0-9]+", "-", spec["field"].lower()).strip("-")[:30]
    rid = run_id or f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{slug}"
    d = Path(base) / rid
    for sub in ("raw", "packets", "evaluations", "outreach_drafts", "export"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "request.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def load_spec(run_dir) -> dict:
    return validate_spec(json.loads((Path(run_dir) / "request.json").read_text(encoding="utf-8")))


def triage_limit(spec) -> int:
    return math.ceil(spec["n"] * spec["triage_multiplier"])
