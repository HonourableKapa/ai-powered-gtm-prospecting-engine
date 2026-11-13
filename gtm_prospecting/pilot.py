"""CLI for the semantic pilot.

  python -m gtm_prospecting.pilot --only handle_a      # DRY RUN (default): no client, no network, no cost
  python -m gtm_prospecting.pilot --run --approve-spend --max-usd 1.50 --only handle_a handle_b [--model ...]

A real run needs BOTH --run and --approve-spend. The cap is an application-level safeguard checked
before each call; it is not a provider-side billing limit.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path

from . import semantic_llm as S
from .pipeline import run as run_pipeline

ORDER = {"NO": 0, "INSUFFICIENT_EVIDENCE": 0, "REVIEW": 1, "YES": 2}


def select(results, handles):
    by = {r["account"]["username"]: r for r in results}
    missing = [h for h in handles if h not in by]
    if missing:
        raise SystemExit(f"handles not in dataset: {missing}")
    return [by[h] for h in handles]


def dry_run(selected, model, cap, out_dir):
    """Pure computation. Constructs no API client."""
    ledger = S.Ledger(Path(out_dir) / "spend_ledger.jsonl")
    rows, cached = [], 0
    for r in selected:
        acc = r["account"]
        payload = S.build_payload(acc)
        e = S.estimate(payload, model)
        hit = (Path(out_dir) / "cache" / f"{S.cache_key(model, payload)}.json").exists()
        cached += hit
        rows.append({"handle": acc["username"], "posts": len(acc["posts"]), "cached": hit, **e})
    live = [x for x in rows if not x["cached"]]
    tot = {k: sum(x[k] for x in live) for k in ("input_tokens_expected", "input_tokens_worst", "output_tokens_expected",
                                                "cost_expected_usd", "cost_worst_case_usd")}
    summary = {"model": model, "pricing_per_mtok": S.PRICING[model], "pricing_source": S.PRICING_SOURCE,
               "effort": S.EFFORT, "max_output_tokens_per_call": S.MAX_OUTPUT_TOKENS, "calls_planned": len(live),
               "cached_profiles_skipped": cached, "per_profile": rows, "totals": tot,
               "already_spent_per_ledger_usd": ledger.spent(), "confirmed_spend_usd": ledger.confirmed(),
               "unknown_billing_exposure_usd": ledger.unknown_exposure(), "cap_usd": cap,
               "worst_case_within_cap": ledger.spent() + tot["cost_worst_case_usd"] <= cap,
               "network_calls_made": 0,
               "caveat": "Token counts are character-based estimates. Cap is application-level, not a provider billing limit."}
    return summary


def print_dry_run(s):
    p = s["pricing_per_mtok"]
    print(f"DRY RUN  model={s['model']}  effort={s['effort']}  (${p['input']}/M in, ${p['output']}/M out; {s['pricing_source']})")
    print(f"{'handle':<13}{'posts':>5}{'in_tok(exp)':>12}{'in_tok(worst)':>14}{'out(exp)':>10}{'cost(exp)':>11}{'cost(worst)':>12}  cached")
    for x in s["per_profile"]:
        print(f"{x['handle']:<13}{x['posts']:>5}{x['input_tokens_expected']:>12,}{x['input_tokens_worst']:>14,}"
              f"{x['output_tokens_expected']:>10,}{'$%.4f' % x['cost_expected_usd']:>11}{'$%.4f' % x['cost_worst_case_usd']:>12}  {x['cached']}")
    t = s["totals"]
    print(f"{'TOTAL':<13}{s['calls_planned']:>5}{t['input_tokens_expected']:>12,}{t['input_tokens_worst']:>14,}"
          f"{t['output_tokens_expected']:>10,}{'$%.4f' % t['cost_expected_usd']:>11}{'$%.4f' % t['cost_worst_case_usd']:>12}")
    print(f"max_tokens per call: {s['max_output_tokens_per_call']:,} | already spent (ledger): ${s['already_spent_per_ledger_usd']:.4f}"
          f" | cap: ${s['cap_usd']:.2f} | worst case within cap: {s['worst_case_within_cap']}")
    print("network calls made: 0 | retries: disabled | model fallback: none | cap = application-level safeguard, not a provider limit")
    print(s["caveat"])


# ------------------------------------------------------------------ comparison with calibration cases
def classify_comparison(case, ai):
    exp, got = case["expected"], ai["recommendation"]
    if exp == got:
        if not case["evidence_in_exports"] and got == "YES":
            return "AGREES_BUT_EVIDENCE_NOT_IN_INPUT", ("AI said YES although the basis for the expected YES is external "
                    "and was not in its input: check it did not credit the bio/persona (possible agreement for the wrong reason)")
        return "AGREES", ""
    if not case["evidence_in_exports"] and ORDER[got] < ORDER[exp]:
        return "DISAGREES_MISSING_EXTERNAL_EVIDENCE", ("expected label depends on evidence outside the exports "
                f"({'; '.join(case['external_evidence_needed'])}); a lower AI result is appropriate for this input")
    if ORDER[got] > ORDER[exp]:
        return "DISAGREES_POSSIBLE_CLASSIFICATION_ERROR", "AI is more positive than the manual label and the evidence in the exports supports; adjudicate"
    return "DISAGREES_POSSIBLE_CLASSIFICATION_ERROR", "AI is more negative than the manual label although the basis is visible in the exports; adjudicate"


def compare(outs, cases_path, out_csv):
    if not Path(cases_path).exists():
        return []   # calibration cases are optional and private to the operator
    cases = {c["handle"]: c for c in json.loads(Path(cases_path).read_text(encoding="utf-8"))["cases"]}
    rows = []
    for o in outs:
        c = cases.get(o["handle"])
        if not c or o["status"] != "ok":
            continue
        a = o["semantic_recommendation"]
        kind, note = classify_comparison(c, a)
        rows.append({"handle": o["handle"], "manual_expected": c["expected"], "ai_recommendation": a["recommendation"],
                     "ai_confidence": a["confidence"], "ai_category": a["expert_category"],
                     "rule_based_expert_status": o["expert_status_manual_pipeline"], "comparison": kind,
                     "evidence_in_exports": c["evidence_in_exports"], "note": note,
                     "ai_missing_verification": " | ".join(a["missing_verification"]),
                     "semantic_flags": " | ".join(o["semantic_flags"]), "validation_warnings": " | ".join(a["validation_warnings"])})
    if rows:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


def context_for(r):
    d = r["decision"]
    return {"expert_status": d["expert_status"], "preliminary_status": d["preliminary_status"],
            "verified_evidence_count": d["verified_evidence_count"],
            "audience_influence": d["audience_influence"], "outreach_suitability": d["outreach_suitability"]}


def main(argv=None, client_factory=S.make_client):
    ap = argparse.ArgumentParser(prog="gtm_prospecting.pilot")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--evidence", default="data/evidence/manual_evidence.json")
    ap.add_argument("--calibration", default="data/calibration/calibration_cases.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--model", default=S.DEFAULT_MODEL)
    ap.add_argument("--max-usd", type=float, default=1.50)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--run", action="store_true", help="make real, paid API calls (also requires --approve-spend)")
    ap.add_argument("--approve-spend", action="store_true")
    a = ap.parse_args(argv)

    sp_out = Path(a.out) / "semantic_pilot"
    sp_out.mkdir(parents=True, exist_ok=True)
    res = run_pipeline(a.raw, a.evidence, a.out)["results"]
    if not a.only:
        raise SystemExit("pass --only <handle> [<handle> ...]: the pilot never selects accounts implicitly (each call costs money)")
    selected = select(res, a.only)
    summary = dry_run(selected, a.model, a.max_usd, sp_out)
    (sp_out / "dry_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_dry_run(summary)
    if not a.run:
        print("\nDry run only. Nothing was sent. Use --run --approve-spend to make paid calls.")
        return 0
    if not a.approve_spend:
        print("\n--run requires --approve-spend. No calls made.")
        return 2
    if not summary["worst_case_within_cap"]:
        print("\nWorst-case estimate exceeds the cap. No calls made.")
        return 3

    pilot = S.Pilot(sp_out, a.model, a.max_usd, client_factory)
    outs, stopped = [], None
    for r in selected:
        try:
            o = pilot.run_profile(r["account"], context_for(r))
        except S.BudgetExceeded as e:
            stopped = str(e)
            break
        except Exception as e:  # API/transport error: stop; never auto-retry
            stopped = f"API error for @{r['account']['username']}: {type(e).__name__}: {e} (not retried)"
            break
        outs.append(o)
        tag = "cache" if o.get("from_cache") else "called"
        print(f"  @{o['handle']}: {o['status']} ({tag})" + (f" {o['failure']}" if o["status"] != "ok" else
              f" -> {o['semantic_recommendation']['recommendation']}/{o['semantic_recommendation']['confidence']}  ${o['usage']['cost_usd']:.4f}"))
    rows = compare(outs, a.calibration, sp_out / "comparison.csv")
    print(f"\nconfirmed spend (ledger): ${pilot.ledger.confirmed():.4f} of ${a.max_usd:.2f} cap (application-level)")
    unk = pilot.ledger.unknown_entries()
    if unk:
        print(f"BILLING STATUS UNKNOWN for {len(unk)} request(s) (upper bound ${pilot.ledger.unknown_exposure():.4f}, "
              "counted against the cap, NOT assumed zero). Check the provider usage page to reconcile.")
    if stopped:
        print("STOPPED:", stopped)
    for x in rows:
        print(f"  {x['handle']:<12} manual={x['manual_expected']:<7} ai={x['ai_recommendation']:<7} {x['comparison']}")
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
