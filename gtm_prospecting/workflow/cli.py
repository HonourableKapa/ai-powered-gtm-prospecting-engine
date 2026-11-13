"""`python -m gtm_prospecting.workflow <command> --run <dir>`  (driven by the /find-crypto-experts skill).

Commands: init, plan, discover*, triage, enrich*, ingest, prepare, export     (* = paid Apify run, needs --approve --max-usd)
Nothing here sends messages, calls the Anthropic API, or starts other Claude Code processes.
"""
from __future__ import annotations
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from ..load import parse_dt
from ..pipeline import run as run_pipeline
from . import apify_rest as A, spec as SP, steps as ST

CACHE_DIR = "data/cache/apify_profiles"
EVIDENCE = "data/evidence/manual_evidence.json"


def _log(run_dir, name, entry):
    p = Path(run_dir) / name
    cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() and name.endswith(".json") else []
    if name.endswith(".jsonl"):
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    else:
        p.write_text(json.dumps(cur + [entry], indent=2), encoding="utf-8")


def analyze(run_dir, evidence=EVIDENCE):
    out = run_pipeline(Path(run_dir) / "raw", evidence, Path(run_dir) / "analysis")
    as_of = parse_dt(out["stats"]["as_of"])
    return out["results"], out["stats"], as_of


def _price(client, fetch):
    return client.actor_price_per_1000() if fetch else (A.ASSUMED_PRICE_PER_1000, "assumed (unverified, pessimistic)")


def _paid_run(run_dir, stage, actor_input, args, client):
    price, src = _price(client, args.fetch_price)
    est = A.estimate(actor_input, price, src)
    A.require_approval(args.approve, args.max_usd, est)
    _log(run_dir, "approvals.json", {"stage": stage, "approved_at": datetime.now(timezone.utc).isoformat(),
                                     "max_usd": args.max_usd, "estimate": est, "input": actor_input})
    try:
        run, items = client.run(actor_input, args.max_usd)
    except A.ApifyRunInterrupted as e:
        _log(run_dir, "apify_runs.jsonl", {"stage": stage, "run_id": e.run_id, "status": "UNKNOWN", "usd": None,
                                           "billing_status": "unknown", "items": None, "note": str(e)})
        raise
    usd = run.get("usageTotalUsd")
    _log(run_dir, "apify_runs.jsonl", {"stage": stage, "run_id": run.get("id"), "status": run.get("status"),
                                       "usd": usd, "billing_status": "confirmed" if usd is not None else "unknown",
                                       "items": len(items), "finished_at": datetime.now(timezone.utc).isoformat()})
    f = Path(run_dir) / "raw" / f"apify_{stage}_{run.get('id')}.json"
    f.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    ST.cache_put(CACHE_DIR, items) if stage == "enrich" else None
    print(f"{stage}: run {run.get('id')} {run.get('status')}, {len(items)} items, "
          f"cost {'$%.4f' % usd if usd is not None else 'UNKNOWN (check Apify Console)'}")
    return run, items


def cmd_init(a, client_factory):
    spec = SP.validate_spec(json.loads(Path(a.request_file).read_text(encoding="utf-8")))
    d = SP.new_run_dir(a.runs_dir, spec, a.run_id)
    print(f"run dir: {d}\nspec OK: field={spec['field']!r} n={spec['n']} window={spec['window_days']}d "
          f"region={'yes' if spec['region'] else 'any'} audience={'user-specified' if spec['audience'] else 'no criteria'}")


def cmd_plan(a, client_factory):
    spec = SP.load_spec(a.run)
    client = client_factory() if a.fetch_price else None
    price, src = _price(client, a.fetch_price)
    q = ST.build_queries(spec)
    d_in, p_in = A.discovery_input(spec, q), A.profile_input(spec, ["x"] * SP.triage_limit(spec))
    de, pe = A.estimate(d_in, price, src), A.estimate(p_in, price, src)
    plan = {"queries": q, "discovery_input": d_in, "discovery_estimate": de,
            "enrichment_upper_bound_profiles": SP.triage_limit(spec), "enrichment_estimate_upper_bound": pe,
            "total_estimated_max_usd": round(de["estimated_max_usd"] + pe["estimated_max_usd"], 4),
            "semantic_evaluation": "in Claude Code session: no API spend",
            "note": "Nothing has been run. Profile enrichment is skipped for accounts already cached (fewer than the bound)."}
    (Path(a.run) / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({k: plan[k] for k in ("queries", "discovery_estimate", "enrichment_estimate_upper_bound", "total_estimated_max_usd")}, indent=2))
    print("No paid call made. Approve with: discover --approve --max-usd <cap>")


def cmd_discover(a, client_factory):
    spec = SP.load_spec(a.run)
    _paid_run(a.run, "discover", A.discovery_input(spec, ST.build_queries(spec)), a, client_factory())


def cmd_triage(a, client_factory):
    spec = SP.load_spec(a.run)
    res, stats, as_of = analyze(a.run)
    seen = ST.seen_handles(Path(a.run).parent, a.run, CACHE_DIR)
    rows = ST.triage(res, spec, CACHE_DIR, as_of, seen=seen)
    need = [r["handle"] for r in rows if r["selected"] and r["needs_paid_profile_scrape"]]
    cached = [r for r in rows if r["selected"] and r["profile_cached"]]
    with open(Path(a.run) / "candidates.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (Path(a.run) / "enrich_plan.json").write_text(json.dumps({"handles_to_scrape": need}, indent=2), encoding="utf-8")
    est = A.estimate(A.profile_input(spec, need or ["x"]) if need else {"maxResults": 0})
    print(f"already seen in earlier runs/cache (not paid for again): {sum('seen_in_earlier_run' in r['exclusion'] for r in rows)}")
    print(f"accounts analysed: {stats['unique_accounts']} | eligible: {sum(r['eligible'] for r in rows)} | "
          f"selected for review: {sum(r['selected'] for r in rows)} | cached profiles reused: {len(cached)} | "
          f"need a paid profile scrape: {len(need)} (upper bound ${est['estimated_max_usd']:.2f} at assumed price)")


def cmd_enrich(a, client_factory):
    spec = SP.load_spec(a.run)
    need = json.loads((Path(a.run) / "enrich_plan.json").read_text(encoding="utf-8"))["handles_to_scrape"]
    if not need:
        print("nothing to enrich (all selected accounts already have profile data)")
        return
    _paid_run(a.run, "enrich", A.profile_input(spec, need), a, client_factory())


def cmd_ingest(a, client_factory):
    print(f"copied {ST.ingest_files(a.run, a.files)} file(s) into {a.run}/raw (no Apify call)")


def _assemble_cached(run_dir, spec, res, skip=frozenset()):
    """Bring cached profile data into the run for accounts that lack it. Accounts in `skip` (already seen in earlier runs,
    so already evaluated there) are left out: they belong to the earlier run's results, not this run's candidates."""
    have = {r["account"]["author_id"] for r in res if r["account"]["has_profile_scrape"]}
    recs = []
    for r in res:
        aid = r["account"]["author_id"]
        if aid not in have and r["account"]["username"].lower() not in skip:
            c = ST.cache_get(CACHE_DIR, aid, spec["cache_ttl_days"])
            recs += c or []
    if recs:
        (Path(run_dir) / "raw" / "cached_profiles.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return len(recs)


def cmd_prepare(a, client_factory):
    spec = SP.load_spec(a.run)
    res, stats, as_of = analyze(a.run)
    seen = ST.seen_handles(Path(a.run).parent, a.run, CACHE_DIR)
    if _assemble_cached(a.run, spec, res, skip=seen):
        res, stats, as_of = analyze(a.run)
    idx = ST.write_packets(a.run, res, spec, a.handles, a.limit, a.include_prelim_no, as_of)
    print(f"prepared {len(idx)} evidence packet(s) in {a.run}/packets (approx {sum(i['approx_tokens'] for i in idx):,} tokens in total)")
    print("Claude Code: read packets/RUBRIC.md, evaluate each packet in this session, write evaluations/<handle>.json, then run `export`.")
    for i in idx:
        print(f"  {i['handle']:<18} {i['posts']:>3} posts  ~{i['approx_tokens']:,} tokens")


def cmd_export(a, client_factory):
    spec = SP.load_spec(a.run)
    res, stats, as_of = analyze(a.run)
    evals = ST.import_evaluations(a.run, res)
    prelim_rows = ST.build_rows(res, evals, {}, spec, as_of)
    tiers = {x["handle"]: x["qualification_tier"] for x in prelim_rows}
    drafts = ST.import_drafts(a.run, res, tiers, spec)
    log = []
    lp = Path(a.run) / "apify_runs.jsonl"
    if lp.exists():
        log = [{"stage": e["stage"], "run_id": e["run_id"], "usd": e["usd"]} for e in map(json.loads, lp.read_text(encoding="utf-8").splitlines())]
    rows = ST.export(a.run, res, evals, drafts, spec, as_of, stats, log)
    print(ST.shortfall_text(rows, spec))
    bad = [h for h, e in evals.items() if e["status"] != "valid"]
    if bad:
        print("REJECTED evaluations:", {h: evals[h]["errors"][:2] for h in bad})
    print(f"wrote {a.run}/export/ranked_prospects.csv, evidence/*.json, run_summary.md. Nothing was sent.")


def cmd_combine(a, client_factory):
    from .combine import combine_runs
    rows = combine_runs(a.runs, a.out, a.primary)
    prim = sum(r["role"] == "PRIMARY" for r in rows)
    print(f"combined {len(rows)} qualified prospects: {prim} primary, {len(rows) - prim} alternate, "
          f"{sum(r['needs_spot_check'] for r in rows)} flagged for spot-check. wrote {a.out}/combined_shortlist.csv. Nothing was sent.")


def main(argv=None, client_factory=A.Apify):
    ap = argparse.ArgumentParser(prog="gtm_prospecting.workflow")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name not in ("init", "combine"):
            p.add_argument("--run", required=True)
        return p
    p = add("combine", cmd_combine)
    p.add_argument("--runs", nargs="+", required=True, help="run directories whose export/ranked_prospects.csv to merge")
    p.add_argument("--out", required=True)
    p.add_argument("--primary", type=int, default=10)
    p = add("init", cmd_init)
    p.add_argument("--request-file", required=True)
    p.add_argument("--runs-dir", default="data/runs")
    p.add_argument("--run-id")
    for n, f in (("plan", cmd_plan), ("discover", cmd_discover), ("enrich", cmd_enrich)):
        p = add(n, f)
        p.add_argument("--fetch-price", action="store_true", help="read-only call to fetch the actor price (needs APIFY_TOKEN)")
        if n != "plan":
            p.add_argument("--approve", action="store_true")
            p.add_argument("--max-usd", type=float)
    add("triage", cmd_triage)
    p = add("ingest", cmd_ingest)
    p.add_argument("files", nargs="+")
    p = add("prepare", cmd_prepare)
    p.add_argument("--handles", nargs="*")
    p.add_argument("--limit", type=int)
    p.add_argument("--include-prelim-no", action="store_true")
    add("export", cmd_export)
    a = ap.parse_args(argv)
    try:
        a.fn(a, client_factory)
    except (A.ApprovalRequired, A.ApifyAuthMissing, A.ApifyRunInterrupted, SP.SpecError) as e:
        print("STOPPED:", e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
