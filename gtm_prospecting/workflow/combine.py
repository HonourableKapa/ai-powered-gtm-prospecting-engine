"""Merge the qualified prospects (tier A) of several runs into one shortlist. No new scoring: rows are ordered by the
existing evidence_score, the first N are PRIMARY and the rest ALTERNATE (replacements if a primary fails final checks).
Nothing is sent; drafts stay DRAFT_NOT_SENT."""
from __future__ import annotations
import csv
import json
import shutil
from pathlib import Path

from .steps import ROW_COLUMNS

EXTRA = ["combined_rank", "role", "needs_spot_check", "source_run"]


def _spend(run_dir: Path):
    p = run_dir / "apify_runs.jsonl"
    if not p.exists():
        return 0.0, 0
    entries = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return round(sum(e["usd"] for e in entries if e.get("usd") is not None), 4), sum(e.get("usd") is None for e in entries)


def combine_runs(run_dirs, out_dir, primary_n=10):
    out = Path(out_dir)
    (out / "evidence").mkdir(parents=True, exist_ok=True)
    (out / "outreach_drafts").mkdir(parents=True, exist_ok=True)
    best, sources = {}, {}
    for rd in map(Path, run_dirs):
        src = rd / "export" / "ranked_prospects.csv"
        if not src.exists():
            raise FileNotFoundError(f"{src} not found: run `export` for {rd.name} first")
        with open(src, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["qualification_tier"] != "A_QUALIFIED":
                    continue
                r["source_run"] = rd.name
                k = r["handle"].lower()
                if k not in best or float(r["evidence_score"]) > float(best[k]["evidence_score"]):
                    best[k] = r
                    sources[k] = rd
    rows = sorted(best.values(), key=lambda r: (-float(r["evidence_score"]), r["handle"].lower()))
    for i, r in enumerate(rows, 1):
        r["combined_rank"] = i
        r["role"] = "PRIMARY" if i <= primary_n else "ALTERNATE"
        r["needs_spot_check"] = r["semantic_confidence"] == "low" or r["promotional_concentration_semantic"] == "high"
        rd = sources[r["handle"].lower()]
        ev = rd / "export" / "evidence" / f"{r['handle']}.json"
        if ev.exists():
            shutil.copy(ev, out / "evidence" / ev.name)
        dr = rd / "outreach_drafts" / f"{r['handle']}.json"
        if dr.exists():
            shutil.copy(dr, out / "outreach_drafts" / dr.name)
    cols = EXTRA + [c for c in ROW_COLUMNS if c != "rank"]
    with open(out / "combined_shortlist.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    prim = [r for r in rows if r["role"] == "PRIMARY"]
    alt = [r for r in rows if r["role"] == "ALTERNATE"]
    spot = [r for r in rows if r["needs_spot_check"]]
    drafts = [r for r in rows if r["outreach_draft_status"] == "DRAFT_NOT_SENT"]
    line = lambda r: (f"| {r['combined_rank']} | @{r['handle']} | {r['semantic_confidence']} | {r['expert_category']} | "
                      f"{r['promotional_concentration_semantic']} | {r['audience_tier']} | {r['source_run']} | "
                      f"{'yes' if r['needs_spot_check'] else ''} | {'draft' if r['outreach_draft_status'] == 'DRAFT_NOT_SENT' else ''} |")
    head = ["| # | Account | Confidence | Category | Promo risk | Audience | Run | Spot-check | Draft |", "|---|---|---|---|---|---|---|---|---|"]
    spend = [(Path(rd).name, *_spend(Path(rd))) for rd in run_dirs]
    summary = ["# Combined qualified-prospect shortlist", "",
               f"Requested {primary_n} primary prospects; {len(prim)} found ({len(rows)} qualified in total, {len(alt)} alternates).",
               ("No shortfall." if len(prim) >= primary_n else f"Shortfall: {primary_n - len(prim)}."), "",
               "Qualified = AI-assessed in the Claude Code session with validated citations, pending human verification. "
               "Ordered by the existing evidence score; expertise, audience and promotional risk are separate columns.", "",
               "## Primary", *head, *map(line, prim), "", "## Alternates (use if a primary fails final outreach checks)", *head, *map(line, alt), "",
               f"## Spot-check first ({len(spot)})", "Low confidence or high promotional concentration; verify before any outreach: " +
               ", ".join(f"@{r['handle']}" for r in spot), "",
               f"## Outreach drafts: {len(drafts)} (all DRAFT_NOT_SENT; placeholders [[SENDER_NAME]] and [[VALUE_PROP]] unresolved)",
               "Nothing has been sent. Sending requires your separate, explicit approval.", "",
               "## Apify spend by run (USD)", *[f"- {n}: ${s:.3f}" + (f" (+{u} run(s) with unknown billing)" if u else "") for n, s, u in spend],
               "- Semantic evaluation was done in the Claude Code session: no Anthropic API spend."]
    (out / "combined_summary.md").write_text("\n".join(summary), encoding="utf-8")
    return rows
