import argparse
from collections import Counter
from .pipeline import run

ap = argparse.ArgumentParser(prog="gtm_prospecting", description="Preliminary X expert triage (offline).")
ap.add_argument("--raw", default="data/raw")
ap.add_argument("--evidence", default="data/evidence/manual_evidence.json")
ap.add_argument("--out", default="out")
a = ap.parse_args()
res = run(a.raw, a.evidence, a.out)
print(res["stats"])
print("expert_status:", dict(Counter(r["decision"]["expert_status"] for r in res["results"])))
print("preliminary:  ", dict(Counter(r["decision"]["preliminary_status"] for r in res["results"])))
print(f"wrote {a.out}/report.csv, posts.csv, evidence/*.json")
