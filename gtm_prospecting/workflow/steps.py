"""Workflow steps that reuse the tested pipeline. Semantic evaluation is done by Claude Code in its own
session: this module prepares evidence packets and VALIDATES the evaluation records Claude Code writes.
Nothing here calls the Anthropic API, launches Claude Code processes, or sends any message."""
from __future__ import annotations
import csv
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .. import semantic_llm as S
from ..load import parse_dt
from ..signals import analysis_evidence
from .spec import SpecError, triage_limit

TIERS = ["A_QUALIFIED", "B_REVIEW", "C_NEEDS_EVALUATION", "D_NOT_QUALIFIED", "E_INSUFFICIENT_EVIDENCE", "X_EXCLUDED"]
TIER_NOTE = {"A_QUALIFIED": "Qualified shortlist (AI-assessed in Claude Code session, citations validated; human verification still pending)",
             "B_REVIEW": "Promising or mixed evidence: needs human review", "C_NEEDS_EVALUATION": "Passed rules; semantic evaluation missing or invalid",
             "D_NOT_QUALIFIED": "Judged outside the expert criteria (reason recorded)", "E_INSUFFICIENT_EVIDENCE": "Not enough data to judge",
             "X_EXCLUDED": "Excluded by the request criteria (activity window, region, audience)"}
EVALUATOR = "claude_code_session"
# Only a qualified account is ever 'ready'; even then a human must approve before anything is sent.
OUTREACH_READINESS = {"A_QUALIFIED": "READY_FOR_DRAFT_REVIEW", "B_REVIEW": "NOT_READY_PENDING_REVIEW",
                      "C_NEEDS_EVALUATION": "NOT_READY_NEEDS_EVALUATION", "D_NOT_QUALIFIED": "EXCLUDED_NOT_QUALIFIED",
                      "E_INSUFFICIENT_EVIDENCE": "NOT_READY_INSUFFICIENT_EVIDENCE", "X_EXCLUDED": "EXCLUDED_BY_REQUEST_CRITERIA"}
PLACEHOLDER = re.compile(r"\[\[[A-Z_]+\]\]")


# ------------------------------------------------------------------ request criteria
MAX_QUERY_CHARS = 480        # X search rejects very long queries


def build_queries(spec):
    """Search strings: the keyword (quoted if a plain phrase) plus the spec's exclusions, e.g. -airdrop -from:handle."""
    excl = [f'-"{t}"' if (" " in t or "$" in t) else f"-{t}" for t in spec.get("exclude_terms", [])]
    excl += [f"-from:{h}" for h in spec.get("exclude_handles", [])]
    out, seen = [], set()
    for k in spec["keywords"]:
        q = f'"{k}"' if " " in k and not any(op in k for op in ('"', ":", "(")) else k
        q = " ".join([q] + excl)
        if len(q) > MAX_QUERY_CHARS:
            raise SpecError(f"query is {len(q)} characters (limit {MAX_QUERY_CHARS}); shorten it or the exclusion lists: {q[:60]}...")
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def region_status(acc, spec):
    if not spec["region"]:
        return "not_requested"
    loc = (acc.get("location") or "").lower()
    if not loc:
        return "unknown"
    return "match" if any(t in loc for t in spec["region"]["terms"]) else "no_match"


def exclusion_reasons(result, spec, as_of):
    """Criteria the user asked for. Unknown values are flagged elsewhere, not treated as failures."""
    acc, eng, sig = result["account"], result["engagement"], result["signals"]
    why = []
    d = sig["days_since_last_post"]
    if d is None or d > spec["window_days"]:
        why.append(f"not_active_in_last_{spec['window_days']}_days")
    if spec["region"] and spec["region"]["required"] and region_status(acc, spec) == "no_match":
        why.append("region_required_no_match")
    a = spec["audience"] or {}
    f, mv = acc.get("followers"), eng.get("median_views")
    if "min_followers" in a and f is not None and f < a["min_followers"]:
        why.append("below_requested_min_followers")
    if "max_followers" in a and f is not None and f > a["max_followers"]:
        why.append("above_requested_max_followers")
    if "min_median_views" in a and mv is not None and mv < a["min_median_views"]:
        why.append("below_requested_min_median_views")
    return why


def best_posts(acc, k=3):
    """The k posts with the most evidence of analysis, best first, as (evidence, post) pairs."""
    scored = sorted(((analysis_evidence(p["text"]), p) for p in acc["posts"]), key=lambda x: -x[0]["score"])
    return scored[:k]


def rule_score(result):
    """Triage priority = how much the best posts show actual analysis (numbers, method, sources, investigation) minus the
    shape of templated promotion. Keyword frequency is deliberately not used: every discovered tweet contains the search
    keywords, so keyword counts only rewarded campaign copy. A hint for who to enrich first, never a verdict."""
    top = best_posts(result["account"])
    if not top:
        return 0.0
    pr = {"HIGH": 1.0, "MEDIUM": 0.5}.get(result["signals"]["promo_risk"], 0.0)   # known only with 3+ posts
    return round(sum(e["score"] for e, _ in top) / len(top) - 2 * pr, 3)


def posts_in_window(acc, as_of, days):
    cut = as_of - timedelta(days=days)
    return sum(1 for p in acc["posts"] if p["created_at"] and parse_dt(p["created_at"]) >= cut)


# ------------------------------------------------------------------ profile cache (plain JSON files; no database)
def cache_put(cache_dir, records, fetched_at=None):
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    by = {}
    for r in records:
        if r.get("source_input_type") == "profile":
            by.setdefault(str(r["author_id"]), []).append(r)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    for aid, recs in by.items():
        (Path(cache_dir) / f"{aid}.json").write_text(json.dumps(
            {"author_id": aid, "handle": recs[0]["author_username"], "fetched_at": fetched_at, "records": recs},
            ensure_ascii=False), encoding="utf-8")
    return len(by)


def cache_get(cache_dir, author_id, ttl_days, now=None):
    p = Path(cache_dir) / f"{author_id}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    now = now or datetime.now(timezone.utc)
    if now - parse_dt(d["fetched_at"]) > timedelta(days=ttl_days):
        return None
    return d["records"]


def ingest_files(run_dir, paths):
    raw = Path(run_dir) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for p in paths:
        shutil.copy(p, raw / Path(p).name)
    return len(paths)


def seen_handles(runs_dir, current_run, cache_dir):
    """Accounts already discovered or researched before (lower-case handles): every author in earlier runs' raw data,
    plus cached profiles. They are not paid for again in a new run."""
    seen = set()
    cur = Path(current_run).resolve()
    for raw in Path(runs_dir).glob("*/raw/*.json"):
        if raw.parents[1].resolve() == cur:
            continue
        try:
            seen |= {str(t.get("author_username", "")).lower() for t in json.loads(raw.read_text(encoding="utf-8"))}
        except (ValueError, TypeError):
            continue
    for f in Path(cache_dir).glob("*.json"):
        try:
            seen.add(str(json.loads(f.read_text(encoding="utf-8")).get("handle", "")).lower())
        except ValueError:
            continue
    seen.discard("")
    return seen


# ------------------------------------------------------------------ triage (decides who is worth a paid profile scrape)
def triage(results, spec, cache_dir, as_of, now=None, seen=frozenset()):
    rows = []
    for r in results:
        acc, sig, dec = r["account"], r["signals"], r["decision"]
        excl = exclusion_reasons(r, spec, as_of)
        rules_no = dec["preliminary_status"] == "NO"
        queries_hit = len({s for p in acc["posts"] for s in p["search_sources"] if s.startswith("search:")})
        cached = bool(cache_get(cache_dir, acc["author_id"], spec["cache_ttl_days"], now)) if not acc["has_profile_scrape"] else False
        already = acc["username"].lower() in seen
        eligible = not excl and not rules_no and not already
        top = best_posts(acc, 1)
        ev, best = top[0] if top else ({"positive": [], "negative": []}, {"text": ""})
        rows.append({"author_id": acc["author_id"], "handle": acc["username"], "eligible": eligible,
                     "exclusion": "|".join(excl + (["rules_preliminary_no"] if rules_no else [])
                                           + (["seen_in_earlier_run"] if already else [])),
                     "score": round(rule_score(r) + 0.25 * queries_hit, 3), "queries_hit": queries_hit,
                     "analysis_signals": "|".join(ev["positive"]), "template_signals": "|".join(ev["negative"]),
                     "evidence_chars": len(best["text"]),
                     "has_profile_scrape": acc["has_profile_scrape"], "profile_cached": cached,
                     "needs_paid_profile_scrape": eligible and not acc["has_profile_scrape"] and not cached})
    # ties are broken by amount of text, then handle: never alphabetically alone (the old cut fell at 'R')
    rows.sort(key=lambda x: (-x["eligible"], -x["score"], -x["evidence_chars"], x["handle"].lower()))
    keep = [x for x in rows if x["eligible"]][:triage_limit(spec)]
    for x in rows:
        x["selected"] = x in keep
    return rows


# ------------------------------------------------------------------ packets for in-session semantic evaluation
def packet_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def rubric_text():
    dims = ", ".join(S.DIMENSIONS)
    return f"""# Evaluation rubric (single source: gtm_prospecting/semantic_llm.py)

You (Claude Code) evaluate each packet in your own session. No API call, no subagents.
Open ONLY the packet files while evaluating. Do not look at follower counts, badges or engagement
(they are intentionally absent from packets and must not influence expertise).

{S.SYSTEM_PROMPT}

## Record to write: `evaluations/<handle>.json`
```json
{{"handle": "<handle>", "evaluator": "{EVALUATOR}", "packet_hash": "<copy from the packet>",
  "assessment": {{
    "expert_category": one of {S.CATEGORIES},
    "recommendation": "YES|REVIEW|NO|INSUFFICIENT_EVIDENCE", "confidence": "low|medium|high", "summary": "...",
    "dimensions": {{ for each of [{dims}]: {{"rating": one of {S.RATING}, "rationale": "...", "supporting_urls": [post urls]}},
                    "promotional_concentration": {{"rating": one of {S.PROMO_LEVEL}, "rationale": "...", "supporting_urls": []}} }},
    "post_assessments": [{{"url": "...", "label": one of {S.POST_LABELS}, "reason": "..."}}],
    "bio_claims": [{{"claim": "...", "verification_needed": "..."}}],
    "supporting_evidence": [{{"url": "...", "point": "..."}}],
    "conflicting_evidence": [{{"url": "...", "point": "..."}}],
    "missing_verification": ["..."],
    "external_checks": [{{"source": "URL or description", "result": one of {[ "corroborated", "partially_corroborated", "contradicted", "not_found", "inaccessible" ]}, "note": "..."}}]
}}}}
```
Validation is automatic and strict: unknown fields, enum violations, and any URL not in the packet are rejected;
a `YES` or `strong` rating without cited post URLs is downgraded (and flagged). Cite only URLs from the packet.
"""


def write_packets(run_dir, results, spec, handles=None, limit=None, include_prelim_no=False, as_of=None):
    run_dir = Path(run_dir)
    by = {r["account"]["username"]: r for r in results}
    if handles:
        chosen = [by[h] for h in handles]
    else:
        cands = []
        for r in results:
            acc, dec = r["account"], r["decision"]
            if not acc["has_profile_scrape"] or len(acc["posts"]) < 3 or exclusion_reasons(r, spec, as_of):
                continue
            obvious_no = (dec["preliminary_status"] == "NO"
                          and (r["signals"]["account_type"] == "company_or_bot_likely" or r["signals"]["crypto_relevance_share"] == 0))
            if obvious_no and not include_prelim_no:
                continue            # promo-keyword NOs still get a full-text evaluation: the keyword screen alone is not trusted
            cands.append(r)
        cands.sort(key=lambda r: (-rule_score(r), r["account"]["username"].lower()))
        chosen = cands[: (limit or triage_limit(spec))]
    pdir = run_dir / "packets"
    pdir.mkdir(exist_ok=True)
    (pdir / "RUBRIC.md").write_text(rubric_text(), encoding="utf-8")
    index = []
    for r in chosen:
        acc = r["account"]
        payload = S.build_payload(acc)
        h = packet_hash(payload)
        (pdir / f"{acc['username']}.json").write_text(json.dumps(
            {"handle": acc["username"], "packet_hash": h, "evaluate_per": "packets/RUBRIC.md", "payload": payload},
            indent=1, ensure_ascii=False), encoding="utf-8")
        index.append({"handle": acc["username"], "packet_hash": h, "posts": len(acc["posts"]),
                      "approx_tokens": int(len(json.dumps(payload, ensure_ascii=False)) / 3.5)})
    (pdir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


# ------------------------------------------------------------------ validation of in-session evaluations
def import_evaluations(run_dir, results):
    run_dir = Path(run_dir)
    by = {r["account"]["username"]: r for r in results}
    vdir = run_dir / "evaluations_validated"
    vdir.mkdir(exist_ok=True)
    out = {}
    for f in sorted((run_dir / "evaluations").glob("*.json")):
        handle = f.stem
        rec = {"handle": handle, "status": "invalid", "errors": [], "file": f.name}
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            rec["errors"].append(f"not valid JSON: {e}")
            out[handle] = rec
            continue
        if handle not in by:
            rec["errors"].append("handle not in this run's analysed accounts")
        else:
            acc = by[handle]["account"]
            payload = S.build_payload(acc)
            if raw.get("handle") != handle:
                rec["errors"].append("handle field does not match file name")
            if raw.get("evaluator") != EVALUATOR:
                rec["errors"].append(f"evaluator must be '{EVALUATOR}'")
            if raw.get("packet_hash") != packet_hash(payload):
                rec["errors"].append("packet_hash missing or stale (evidence changed or packet not used)")
            if not rec["errors"]:
                try:
                    warnings = S.validate_assessment(raw.get("assessment"), payload)
                    fixed, enf = S.enforce_citations(raw["assessment"], payload)
                    flags = (["YES_DOWNGRADED_MISSING_CITATIONS"] if any(x["field"] == "recommendation" for x in enf) else []) \
                        + (["STRONG_CLAIM_DOWNGRADED_MISSING_CITATION"] if any(x["field"].startswith("dimensions.") for x in enf) else [])
                    rec.update(status="valid", assessment=fixed, assessment_raw=raw["assessment"] if enf else None,
                               citation_enforcement=enf, warnings=warnings, flags=flags, evaluator=EVALUATOR,
                               packet_hash=raw["packet_hash"])
                except S.InvalidAssessment as e:
                    rec["errors"] += e.errors
                except (TypeError, KeyError) as e:
                    rec["errors"].append(f"malformed assessment: {type(e).__name__}: {e}")
        (vdir / f"{handle}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        out[handle] = rec
    return out


# ------------------------------------------------------------------ outreach drafts (validated, never sent)
def import_drafts(run_dir, results, tiers, spec):
    run_dir = Path(run_dir)
    by = {r["account"]["username"]: r for r in results}
    out = {}
    for f in sorted((run_dir / "outreach_drafts").glob("*.json")):
        h = f.stem
        d = {"handle": h, "status": "rejected", "errors": []}
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            d["errors"].append(f"not valid JSON: {e}")
            out[h] = d
            continue
        if h not in by:
            d["errors"].append("handle not in this run")
        else:
            urls = {p["tweet_url"] for p in by[h]["account"]["posts"]}
            tier = tiers.get(h)
            if raw.get("status") != "DRAFT_NOT_SENT":
                d["errors"].append("status must be exactly DRAFT_NOT_SENT")
            if tier != "A_QUALIFIED" and not (tier == "B_REVIEW" and spec["draft_for_review_tier"]):
                d["errors"].append(f"drafts are only produced for qualified prospects (tier is {tier})")
            if len(raw.get("body", "")) < 40:
                d["errors"].append("body too short")
            ref = raw.get("referenced_urls") or []
            if not ref or not set(ref) <= urls:
                d["errors"].append("referenced_urls must be non-empty and belong to this account's collected posts")
        if not d["errors"]:
            ph = sorted(set(PLACEHOLDER.findall(raw["subject"] + " " + raw["body"])))
            d.update(status="DRAFT_NOT_SENT", subject=raw.get("subject", ""), body=raw["body"],
                     referenced_urls=raw["referenced_urls"], unresolved_placeholders=ph,
                     ready_to_review=not ph, grounded_in=raw.get("grounded_in", ""))
        out[h] = d
    return out


# ------------------------------------------------------------------ ranking
def _post_urls(urls):
    return {u for u in urls if "/status/" in u}


def evidence_score(result, ev):
    if not ev:
        return rule_score(result)
    a = ev["assessment"]
    cited = _post_urls([e["url"] for e in a["supporting_evidence"]]
                       + [u for d in a["dimensions"].values() for u in d["supporting_urls"]])
    conf = {"high": 3, "medium": 2, "low": 1}[a["confidence"]]
    cons = {"strong": 2, "moderate": 1}.get(a["dimensions"]["topical_consistency"]["rating"], 0)
    promo = {"high": 3, "moderate": 1.5}.get(a["dimensions"]["promotional_concentration"]["rating"], 0)
    return round(10 * conf + 2 * min(len(cited), 8) + 3 * cons - 5 * promo, 2)


def assign_tier(result, ev, spec, as_of):
    excl = exclusion_reasons(result, spec, as_of)
    dec = result["decision"]
    if excl:
        return "X_EXCLUDED", "; ".join(excl)
    prelim = dec["preliminary_status"]
    if ev and ev["status"] == "valid":
        rec = ev["assessment"]["recommendation"]
        # a valid evaluation of the full posts outranks the keyword-level rule screen; the conflict stays visible
        note = (f" (overrides keyword-rule NO: {'; '.join(dec['preliminary_reasons'])})"
                if prelim == "NO" and rec in ("YES", "REVIEW") else "")
        if rec == "YES":
            return "A_QUALIFIED", "semantic YES with validated citations" + note
        if rec == "REVIEW":
            return "B_REVIEW", "semantic REVIEW: specific uncertainty named in missing_verification" + note
        if rec == "INSUFFICIENT_EVIDENCE":
            return "E_INSUFFICIENT_EVIDENCE", "semantic INSUFFICIENT_EVIDENCE: cannot qualify or reject on the available evidence"
        return "D_NOT_QUALIFIED", "semantic NO"
    if prelim == "NO":
        return "D_NOT_QUALIFIED", "; ".join(dec["preliminary_reasons"])
    if prelim == "INSUFFICIENT_EVIDENCE":
        return "E_INSUFFICIENT_EVIDENCE", "; ".join(dec["preliminary_reasons"])
    return "C_NEEDS_EVALUATION", ("evaluation invalid: " + "; ".join(ev["errors"][:2])) if ev else "no semantic evaluation yet"


ROW_COLUMNS = [
    "rank", "handle", "display_name", "profile_url", "bio", "bio_status", "followers", "location", "region_status",
    "account_created_at", "x_verified_badge_info_only",
    "qualification_tier", "qualified", "within_requested_count", "tier_reason", "semantic_recommendation",
    "semantic_confidence", "expert_category", "expert_status_manual", "preliminary_status_rules",
    "specialized_knowledge", "original_research_or_development", "analytical_depth", "topical_consistency",
    "credibility_signals", "crypto_defi_relevance", "promotional_concentration_semantic", "promo_risk_rules",
    "evidence_urls", "conflicting_urls", "missing_verification", "external_checks", "citation_flags",
    "posts_in_sample", "posts_in_window", "days_since_last_post", "median_views", "median_engagement_per_view",
    "engagement_flags", "audience_tier", "outreach_priority", "outreach_fit_if_qualified", "outreach_readiness", "outreach_draft_status",
    "outreach_draft", "outreach_draft_placeholders", "evidence_score", "evidence_json"]


def build_rows(results, evals, drafts, spec, as_of):
    rows = []
    for r in results:
        acc, eng, sig, dec = r["account"], r["engagement"], r["signals"], r["decision"]
        h = acc["username"]
        ev = evals.get(h)
        tier, why = assign_tier(r, ev, spec, as_of)
        a = ev["assessment"] if ev and ev["status"] == "valid" else None
        dr = drafts.get(h)
        sup = sorted(_post_urls([e["url"] for e in a["supporting_evidence"]] + [u for d in a["dimensions"].values() for u in d["supporting_urls"]])) if a else []
        row = {
            "handle": h, "display_name": acc.get("name"), "profile_url": f"https://x.com/{h}", "bio": acc.get("bio"),
            "bio_status": "self_reported_unverified" if acc.get("bio") else "unknown", "followers": acc.get("followers"),
            "location": acc.get("location"), "region_status": region_status(acc, spec),
            "account_created_at": acc.get("account_created_at"), "x_verified_badge_info_only": acc.get("verified_badge"),
            "qualification_tier": tier, "qualified": tier == "A_QUALIFIED", "tier_reason": why,
            "semantic_recommendation": a["recommendation"] if a else None, "semantic_confidence": a["confidence"] if a else None,
            "expert_category": a["expert_category"] if a else None, "expert_status_manual": dec["expert_status"],
            "preliminary_status_rules": dec["preliminary_status"],
            "promo_risk_rules": sig["promo_risk"], "evidence_urls": sup,
            "conflicting_urls": sorted({e["url"] for e in a["conflicting_evidence"] if e["url"]}) if a else [],
            "missing_verification": a["missing_verification"] if a else dec["missing_information"],
            "external_checks": [f"{c['result']}: {c['source']}" for c in a.get("external_checks", [])] if a else [],
            "citation_flags": ev.get("flags", []) if ev else [],
            "posts_in_sample": eng["n_posts"], "posts_in_window": posts_in_window(acc, as_of, spec["window_days"]),
            "days_since_last_post": sig["days_since_last_post"], "median_views": eng["median_views"],
            "median_engagement_per_view": eng["median_engagement_per_view"],
            "engagement_flags": eng["engagement_flags"], "audience_tier": dec["audience_influence"]["follower_tier"],
            # outreach status follows the qualification TIER, never audience or fit: a rejected account cannot look ready
            "outreach_priority": dec["outreach_suitability"]["rating"] if tier == "A_QUALIFIED" else "NOT_APPLICABLE",
            "outreach_fit_if_qualified": dec["outreach_suitability"]["rating"],
            "outreach_readiness": OUTREACH_READINESS[tier],
            "outreach_draft_status": dr["status"] if dr and dr["status"] == "DRAFT_NOT_SENT" else ("REJECTED: " + "; ".join(dr["errors"]) if dr else "NO_DRAFT"),
            "outreach_draft": (dr.get("subject", "") + "\n\n" + dr["body"]) if dr and dr["status"] == "DRAFT_NOT_SENT" else None,
            "outreach_draft_placeholders": dr.get("unresolved_placeholders") if dr and dr["status"] == "DRAFT_NOT_SENT" else None,
            "evidence_score": evidence_score(r, ev if a else None),
            "evidence_json": f"evidence/{h}.json",
        }
        for d in S.DIMENSIONS:
            row[d] = a["dimensions"][d]["rating"] if a else None
        row["promotional_concentration_semantic"] = a["dimensions"]["promotional_concentration"]["rating"] if a else None
        rows.append(row)
    rows.sort(key=lambda x: (TIERS.index(x["qualification_tier"]), -x["evidence_score"], x["handle"].lower()))
    rank, qualified_seen = 0, 0
    for x in rows:
        if x["qualification_tier"] == "X_EXCLUDED":
            x["rank"], x["within_requested_count"] = None, False
            continue
        rank += 1
        x["rank"] = rank
        if x["qualified"]:
            qualified_seen += 1
        x["within_requested_count"] = bool(x["qualified"] and qualified_seen <= spec["n"])
    return rows


def shortfall_text(rows, spec):
    q = [x for x in rows if x["qualified"]]
    got = min(len(q), spec["n"])
    cnt = {t: sum(x["qualification_tier"] == t for x in rows) for t in TIERS}
    if got >= spec["n"]:
        return f"Requested {spec['n']} qualified prospects; {got} returned. No shortfall."
    parts = [f"Requested {spec['n']} qualified prospects; only {got} met the bar. Shortfall: {spec['n'] - got}.",
             "Funnel: " + ", ".join(f"{t}={c}" for t, c in cnt.items() if c) + "."]
    if cnt["C_NEEDS_EVALUATION"]:
        parts.append(f"{cnt['C_NEEDS_EVALUATION']} account(s) passed rules but have no valid semantic evaluation: evaluate them first.")
    if cnt["E_INSUFFICIENT_EVIDENCE"]:
        parts.append(f"{cnt['E_INSUFFICIENT_EVIDENCE']} account(s) lack enough evidence either way (not rejected); more posts or inspectable work could qualify them.")
    if cnt["B_REVIEW"]:
        parts.append(f"{cnt['B_REVIEW']} account(s) are in the REVIEW tier (not counted as qualified); a human can review them or the evidence can be extended.")
    parts.append("Options (each paid step needs your approval): broaden keywords, extend the activity window, discover more "
                 "candidates, or profile-scrape more accounts. No results were invented to fill the gap.")
    return " ".join(parts)


# ------------------------------------------------------------------ export
def export(run_dir, results, evals, drafts, spec, as_of, stats, apify_log=None):
    run_dir = Path(run_dir)
    exp = run_dir / "export"
    (exp / "evidence").mkdir(parents=True, exist_ok=True)
    rows = build_rows(results, evals, drafts, spec, as_of)
    with open(exp / "ranked_prospects.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ROW_COLUMNS)
        w.writeheader()
        for x in rows:
            w.writerow({k: ("|".join(map(str, v)) if isinstance(v, list) else v) for k, v in ((c, x.get(c)) for c in ROW_COLUMNS)})
    by = {r["account"]["username"]: r for r in results}
    for x in rows:
        h = x["handle"]
        acc = by[h]["account"]
        rb = run_dir / "analysis" / "evidence" / f"{acc['author_id']}.json"
        (exp / "evidence" / f"{h}.json").write_text(json.dumps({
            "summary_row": x, "request": {k: spec[k] for k in ("field", "n", "window_days", "region", "audience")},
            "semantic_evaluation": evals.get(h), "outreach_draft": drafts.get(h),
            "rule_based_record": json.loads(rb.read_text(encoding="utf-8")) if rb.exists() else None,
            "dimensions_kept_separate": {"expertise": x["qualification_tier"], "audience_influence": x["audience_tier"],
                                         "outreach_priority": x["outreach_priority"]},
            "caveat": "Preliminary AI-assisted assessment. A qualified shortlist entry still requires human verification; "
                      "audience size is not evidence of expertise; no outreach has been sent."},
            indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    cnt = {t: sum(x["qualification_tier"] == t for x in rows) for t in TIERS}
    spend = apify_log or []
    summary = [f"# Run summary: {spec['field']}", "",
               f"**Request:** {spec['request_text'] or '(see request.json)'}",
               f"**Wanted:** {spec['n']} qualified | **Window:** last {spec['window_days']} days | "
               f"**Region:** {(spec['region'] or {}).get('terms', 'any')} | **Audience criteria:** {spec['audience'] or 'none requested'}", "",
               "## Result", shortfall_text(rows, spec), "",
               "## Funnel",
               f"- Raw rows: {stats['raw_rows']} -> unique posts {stats['unique_posts']} -> unique accounts {stats['unique_accounts']}",
               *[f"- {t}: {cnt[t]} ({TIER_NOTE[t]})" for t in TIERS],
               f"- Semantic evaluations validated: {sum(1 for e in evals.values() if e['status'] == 'valid')}, "
               f"rejected: {sum(1 for e in evals.values() if e['status'] != 'valid')}",
               f"- Outreach drafts (DRAFT_NOT_SENT): {sum(1 for d in drafts.values() if d['status'] == 'DRAFT_NOT_SENT')}", "",
               "## Cost",
               ("- Apify runs: " + "; ".join(f"{s['stage']} run {s.get('run_id')}: ${s['usd']:.4f}" if s.get('usd') is not None
                                             else f"{s['stage']} run {s.get('run_id')}: billing UNKNOWN" for s in spend)) if spend
               else "- Apify: no paid runs in this export (local data only).",
               "- Semantic evaluation: performed inside the Claude Code session (subscription); no Anthropic API spend.", "",
               "## Caveats",
               "- Qualified = AI-assessed with validated citations, pending human verification. It is not a verified expert judgment.",
               "- Expertise, audience influence and outreach priority are separate columns; follower counts never feed expertise.",
               "- Region is a soft match on self-reported location; unknown locations are kept and flagged.",
               "- No message has been sent and none will be without your explicit approval."]
    (exp / "run_summary.md").write_text("\n".join(summary), encoding="utf-8")
    return rows
