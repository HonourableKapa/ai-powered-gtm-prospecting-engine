"""Two-stage classification.

preliminary_status: rule-based triage (REVIEW / INSUFFICIENT_EVIDENCE / NO). Never YES.
expert_status: YES only with manually verified, source-URL-backed, substantial evidence (yes_gate).
Without that it mirrors the preliminary triage. Neither is a definitive expert judgment.
Outreach priority is computed separately and nothing is ever sent.
"""
from __future__ import annotations
from .evidence import YES_CAPABLE, manual_to_item
from . import metrics as M

ASSESSMENT_BASIS = "rule_based_preliminary"


def preliminary(sig, eng):
    """Return (status, reasons). NO only for obvious cases; thin evidence is never auto-rejected."""
    n = eng["n_posts"]
    if sig["account_type"] == "company_or_bot_likely":
        return "NO", [f"outside individual-expert criteria: {sig['account_type_reason']}"]
    if sig["promo_risk"] == "HIGH" and sig["technical_post_share"] < 0.2 and sig["bio_promo_language"]:
        return "NO", [
            f"high promotional concentration (@{sig['top_mentioned_entity']} in "
            f"{sig['top_entity_post_share']:.0%} of posts), bio advertises promotion services, "
            "and little technical content in the reviewed sample"]
    if n < 3:
        return "INSUFFICIENT_EVIDENCE", [f"only {n} post(s) in sample"]
    if sig["crypto_relevance_share"] == 0:
        return "NO", ["no crypto-relevant content in the reviewed sample"]
    if n < M.SMALL_SAMPLE and sig["technical_post_share"] == 0 and sig["built_claim_posts"] == 0:
        return "INSUFFICIENT_EVIDENCE", [f"small sample (n={n}) with no technical or build signals"]
    reasons = [f"crypto-relevant in {sig['crypto_relevance_share']:.0%} of posts"]
    if sig["technical_post_share"] > 0:
        reasons.append(f"technical keyword signal in {sig['technical_post_share']:.0%} of posts (hint only)")
    if sig["built_claim_posts"]:
        reasons.append(f"{sig['built_claim_posts']} self-reported 'built' post(s), unverified")
    if sig["promo_risk"] in ("HIGH", "MEDIUM"):
        reasons.append(f"promotional risk {sig['promo_risk']}: needs human review"
                       + (f" ({sig['entity_coverage_hint']})" if sig["entity_coverage_hint"] else ""))
    return "REVIEW", reasons


def yes_gate(verified):
    """YES needs substantial, attributable, verified evidence; third-party is not the only route."""
    capable = [r for r in verified if r["evidence_type"] in YES_CAPABLE]
    substantial = [r for r in capable if r["strength"] == "substantial"]
    moderate_types = {r["evidence_type"] for r in capable if r["strength"] == "moderate"}
    if substantial:
        return True, f"verified substantial evidence (record {substantial[0]['record_id']})"
    if len(moderate_types) >= 2:
        return True, "verified moderate evidence of two different types"
    return False, "verified evidence does not reach 'substantial' (or two distinct moderate types)"


def audience_influence(acc, eng):
    """Descriptive reach only. Follower count and views say nothing about expertise."""
    f = acc.get("followers")
    mv = eng["median_views"]
    tier = ("unknown" if f is None else "nano(<1k)" if f < 1_000 else "micro(1k-10k)" if f < 10_000
            else "mid(10k-100k)" if f < 100_000 else "large(100k+)")
    return {"followers": f, "follower_tier": tier, "median_views_per_post": mv,
            "reach_points": 0 if f is None else 2 if f >= 10_000 else 1 if f >= 1_000 else 0,
            "not_evidence_of_expertise": True,
            "note": "Followers/views describe audience size only; X verification badge is not used."}


def outreach_suitability(acc, sig, eng, status, aud):
    """Outreach fit: topic relevance, reach, collaboration signals, promo conflicts. Separate from expertise.
    Relevance is proxied by crypto/DeFi topical focus until a custom ICP is configured."""
    d = sig["days_since_last_post"]
    fit = (1 if sig["crypto_relevance_share"] >= 0.5 else 0) + (1 if sig["technical_post_share"] >= 0.3 else 0)
    recent = 1 if d is not None and d <= 14 else 0
    collab = 1 if sig["collaboration_signals"] else 0
    conflict = {"HIGH": 2, "MEDIUM": 1}.get(sig["promo_risk"], 0)
    score = fit + recent + collab + aud["reach_points"] - conflict
    rating = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"
    readiness = ("ELIGIBLE" if status == "YES" else "EXCLUDED" if status == "NO" else "PENDING_QUALIFICATION")
    return {"rating": rating, "score": score, "readiness": readiness,
            "components": {"topic_fit_points": fit, "recent_activity": bool(recent),
                           "collaboration_signals": sig["collaboration_signals"],
                           "reach_points": aud["reach_points"], "promotional_conflict_penalty": conflict,
                           "promo_conflict_detail": (f"top entity @{sig['top_mentioned_entity']} in "
                                                     f"{sig['top_entity_post_share']:.0%} of posts"
                                                     if conflict and sig["top_mentioned_entity"] else None)},
            "note": "Rating is provisional until expert_status is YES. Nothing is sent by this system."}


def explain(acc, sig, eng, status, prelim, reasons, verified, pending):
    """How bio, posting history and supporting evidence each contributed to the decision."""
    bio = acc.get("bio")
    if bio:
        claims = sig["bio_roles_self_reported"] + [f"@{h}" for h in sig["bio_affiliations_self_reported"]]
        b = (f"Bio ({acc['bio_source']}) says \"{' '.join(bio.split())}\" Treated as SELF-REPORTED"
             + (f" (role/affiliation terms: {', '.join(claims)})" if claims else "")
             + ". It guided triage but cannot support YES."
             + (" Bio advertises promotion services, which counts as a risk signal." if sig["bio_promo_language"] else ""))
    else:
        b = "No bio in the exports (field unknown); bio contributed nothing to this decision."
    h = (f"{eng['n_posts']} post(s) over {sig['sample_span_days']} day(s) in the sample; "
         f"crypto-relevant {sig['crypto_relevance_share']:.0%}; technical-keyword {sig['technical_post_share']:.0%}; "
         f"original posts {sig['original_post_share']:.0%} vs quote-commentary {sig['quote_post_share']:.0%}; "
         f"promotional risk {sig['promo_risk']}"
         + (f" (@{sig['top_mentioned_entity']} in {sig['top_entity_post_share']:.0%} of posts)" if sig["top_mentioned_entity"] and sig["promo_risk"] != "UNKNOWN" else "")
         + ". Keyword signals are hints, not proof.")
    e = (f"{len(verified)} manually verified and {len(pending)} pending evidence record(s). "
         "Independent references, GitHub activity and published work: unknown (not collected in the exports).")
    return {"bio": b, "posting_history": h, "supporting_evidence": e,
            "conclusion": f"preliminary={prelim}, expert_status={status}. " + "; ".join(reasons)}


def classify(acc, eng, sig, sig_items, manual_recs, semantic_evaluators=()):
    items = list(sig_items)
    dim_notes = {}
    for ev in semantic_evaluators:
        extra, notes = ev.evaluate(acc, sig)
        for it in extra:
            it.origin = "semantic_evaluator"
        items += extra
        dim_notes[ev.name] = notes
    items += [manual_to_item(r) for r in manual_recs]

    verified = [r for r in manual_recs if r["verification_status"] == "verified"]
    pending = [r for r in manual_recs if r["verification_status"] == "pending"]
    prelim, prelim_reasons = preliminary(sig, eng)

    missing = []
    if not acc.get("bio"):
        missing.append("no_bio")
    if not acc["has_profile_scrape"]:
        missing.append("no_profile_scrape")
    if eng["n_posts"] < M.SMALL_SAMPLE:
        missing.append(f"small_post_sample(n={eng['n_posts']})")
    if not verified:
        missing.append("no_manually_verified_evidence")
    if pending:
        missing.append(f"{len(pending)}_pending_manual_evidence_record(s)")
    if sig["account_type"] == "unknown":
        missing.append("account_type_unknown")

    status, confidence, reasons = prelim, "low", list(prelim_reasons)
    if verified and sig["account_type"] != "company_or_bot_likely":
        ok, why = yes_gate(verified)
        if ok and sig["promo_risk"] == "HIGH":
            status = "REVIEW"
            reasons += [why, "high promotional concentration conflicts with verified evidence: human decision required"]
        elif ok:
            status = "YES"
            confidence = "high" if any(r["strength"] == "substantial" for r in verified) else "medium"
            reasons = [why] + reasons
        else:
            status = "REVIEW" if prelim == "INSUFFICIENT_EVIDENCE" else prelim
            reasons.append(why)
    elif prelim == "REVIEW":
        reasons.append("no manually verified evidence yet; cannot be YES")
    if prelim == "NO" and status == "NO":
        confidence = "medium" if eng["n_posts"] >= M.SMALL_SAMPLE else "low"
        reasons.append("NO applies to the reviewed sample only; revisit if new evidence is added")

    aud = audience_influence(acc, eng)
    suit = outreach_suitability(acc, sig, eng, status, aud)
    how = explain(acc, sig, eng, status, prelim, reasons, verified, pending)
    return {
        "preliminary_status": prelim,
        "preliminary_reasons": prelim_reasons,
        "expert_status": status,
        "expert_confidence": confidence,
        "decision_reasons": reasons,
        "assessment_basis": ASSESSMENT_BASIS,
        "semantic_evaluation": "ran" if semantic_evaluators else "not_run",
        "missing_information": missing,
        "conflicting_evidence_count": sum(i.polarity == "conflicting" for i in items),
        "verified_evidence_count": len(verified),
        "pending_evidence_count": len(pending),
        "dimensions": {
            "specialized_knowledge": {"preliminary_signal": f"technical keyword share {sig['technical_post_share']}",
                                      "verified": bool(verified)},
            "original_or_technical_work": {"self_reported_build_posts": sig["built_claim_posts"],
                                           "verified": bool(verified)},
            "consistency": {"posts": eng["n_posts"], "active_days": sig["active_days"],
                            "crypto_relevance_share": sig["crypto_relevance_share"]},
            "credibility": {"bio_source": acc["bio_source"], "verified_records": len(verified)},
            "promotional_concentration": {"risk": sig["promo_risk"], "top_entity": sig["top_mentioned_entity"],
                                          "top_entity_post_share": sig["top_entity_post_share"],
                                          "hint": sig["entity_coverage_hint"]},
            "account_type": {"value": sig["account_type"], "reason": sig["account_type_reason"]},
        },
        "semantic_notes": dim_notes,
        "audience_influence": aud,
        "outreach_suitability": suit,
        "outreach_eligible": status == "YES",
        "decision_explanation": how,
        "evidence_items": items,
    }
