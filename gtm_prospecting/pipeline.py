"""Orchestration + CSV/JSON output."""
from __future__ import annotations
import csv
import json
from pathlib import Path
from . import load, merge, metrics, signals, classify, evidence

PROFILE_COLUMNS = ["author_id", "username", "name", "bio", "bio_source", "account_created_at",
                   "x_verified_badge", "followers", "location"]
REPORT_COLUMNS = PROFILE_COLUMNS + [
    # content
    "n_posts", "sample_span_days", "posts_per_week_in_sample", "original_post_share", "quote_post_share",
    "crypto_relevance_share", "technical_post_share", "built_claim_posts", "linked_domains",
    "github_links_in_posts", "bio_roles_self_reported", "bio_affiliations_self_reported",
    "top_mentioned_entity", "top_entity_post_share", "promo_marker_share", "project_mention_share", "promo_risk",
    # engagement (descriptive only)
    "n_posts_with_views", "median_views", "median_likes", "median_replies", "median_reposts", "median_quotes",
    "median_engagement_per_view", "engagement_flags",
    # credibility (unknown stays unknown)
    "independent_references", "github_activity", "published_work",
    "verified_evidence_count", "pending_evidence_count", "conflicting_evidence_count",
    # assessment 1: expertise
    "account_type", "preliminary_status", "preliminary_reasons", "expert_status", "expert_confidence",
    "decision_reasons", "assessment_basis",
    # assessment 2: audience influence (not expertise)
    "follower_tier", "audience_note",
    # assessment 3: outreach suitability (nothing is sent)
    "outreach_suitability", "outreach_score", "outreach_readiness", "outreach_eligible", "collaboration_signals",
    "decision_explanation", "missing_information", "evidence_json",
]
POST_COLUMNS = ["tweet_id", "tweet_url", "author_id", "author_username", "created_at", "text",
                "search_sources", "source_files", "view_count", "like_count", "reply_count",
                "retweet_count", "quote_count", "bookmark_count", "is_quote", "urls", "mentions",
                "hashtags", "duplicate_count", "metrics_differ_across_scrapes", "scraped_at", "media_type", "media_urls",
                "video_url", "conversation_id", "in_reply_to_tweet_id", "is_sensitive", "source_app", "quoted_tweet_id",
                "quoted_author_username", "quoted_tweet_text", "retweeted_tweet_id", "retweeted_author_username", "lang"]


def run(raw_dir, evidence_path, out_dir, semantic_evaluators=()):
    rows = load.load_raw(raw_dir)
    posts = merge.dedupe_posts(rows)
    accounts = merge.build_accounts(rows, posts)
    manual = evidence.load_manual(evidence_path)
    as_of = max(load.parse_dt(r["scraped_at"]) for r in rows)  # reproducible reference date

    results = []
    for aid, acc in sorted(accounts.items(), key=lambda kv: kv[1]["username"].lower()):
        eng = metrics.engagement(acc["posts"])
        sig, sig_items = signals.compute(acc, as_of)
        dq = []
        if not acc["bio"]:
            dq.append("NO_BIO")
        if not acc["has_profile_scrape"]:
            dq.append("NO_PROFILE_SCRAPE")
        if any(p["metrics_differ_across_scrapes"] for p in acc["posts"]):
            dq.append("METRICS_DIFFER_ACROSS_SCRAPES")
        if len(acc["usernames_seen"]) > 1:
            dq.append("USERNAME_CHANGED")
        dec = classify.classify(acc, eng, sig, sig_items, evidence.records_for(acc, manual), semantic_evaluators)
        results.append({"account": acc, "engagement": eng, "data_quality_flags": dq,
                        "signals": sig, "decision": dec})

    out = Path(out_dir)
    (out / "evidence").mkdir(parents=True, exist_ok=True)
    stats = {"raw_rows": len(rows), "unique_posts": len(posts), "unique_accounts": len(accounts),
             "as_of": as_of.isoformat()}
    write_outputs(out, posts, results, stats)
    return {"stats": stats, "results": results, "posts": posts}


def _j(v):
    return " | ".join(map(str, v)) if isinstance(v, (list, tuple)) else v


def write_outputs(out: Path, posts, results, stats):
    with open(out / "posts.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, POST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for p in posts:
            w.writerow({k: _j(p.get(k)) for k in POST_COLUMNS})
    with open(out / "report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, REPORT_COLUMNS)
        w.writeheader()
        for r in results:
            a, e, sg, d = r["account"], r["engagement"], r["signals"], r["decision"]
            row = {**a, **e, **sg, **d}
            row["x_verified_badge"] = a.get("verified_badge")
            row["engagement_flags"] = e["engagement_flags"] + r["data_quality_flags"]
            row["follower_tier"] = d["audience_influence"]["follower_tier"]
            row["audience_note"] = d["audience_influence"]["note"]
            su = d["outreach_suitability"]
            row.update(outreach_suitability=su["rating"], outreach_score=su["score"], outreach_readiness=su["readiness"])
            row["decision_explanation"] = " || ".join(f"{k.upper()}: {v}" for k, v in d["decision_explanation"].items())
            row["evidence_json"] = f"evidence/{a['author_id']}.json"
            # a missing profile field is written as the word 'unknown', never guessed or left blank
            w.writerow({k: ("unknown" if k in PROFILE_COLUMNS and row.get(k) in (None, "") else _j(row.get(k)))
                        for k in REPORT_COLUMNS})
    for r in results:
        a, d = r["account"], r["decision"]
        rec = {
            "author_id": a["author_id"], "username": a["username"], "profile_url": f"https://x.com/{a['username']}",
            "profile": {
                "handle": a["username"], "display_name": a.get("name"), "bio": a.get("bio"),
                "bio_source": a["bio_source"], "bio_status": "self_reported_unverified" if a.get("bio") else "unknown",
                "account_created_at": a.get("account_created_at"), "x_verified_badge": a.get("verified_badge"),
                "followers": a.get("followers"), "location": a.get("location"), "avatar_url": a.get("avatar_url"),
                "has_profile_scrape": a["has_profile_scrape"],
                "unknown_fields": [k for k, v in (("bio", a.get("bio")), ("location", a.get("location")),
                                                  ("followers", a.get("followers")), ("account_created_at", a.get("account_created_at")))
                                   if v in (None, "")],
                "not_collected_in_exports": signals.NOT_COLLECTED,
            },
            "preliminary_status": d["preliminary_status"], "expert_status": d["expert_status"],
            "expert_confidence": d["expert_confidence"], "assessment_basis": d["assessment_basis"],
            "semantic_evaluation": d["semantic_evaluation"],
            "reasons": d["decision_reasons"], "missing_information": d["missing_information"],
            "data_quality_flags": r["data_quality_flags"],
            "engagement": r["engagement"], "signals": r["signals"], "dimensions": d["dimensions"],
            "assessments": {
                "expertise": {"preliminary_status": d["preliminary_status"], "expert_status": d["expert_status"],
                              "confidence": d["expert_confidence"]},
                "audience_influence": d["audience_influence"],
                "outreach_suitability": {**d["outreach_suitability"], "eligible": d["outreach_eligible"]},
            },
            "decision_explanation": d["decision_explanation"],
            "evidence": [i.to_dict() for i in d["evidence_items"]],
            "post_urls": [p["tweet_url"] for p in a["posts"]],
            "caveat": "Preliminary rule-based assessment; engagement is descriptive and not proof of expertise.",
        }
        (out / "evidence" / f"{a['author_id']}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "run_summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
