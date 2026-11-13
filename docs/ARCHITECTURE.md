# Architecture

The engine is a sequence of small, separately testable steps. Deterministic code does everything that can be code; the one judgment step (expertise evaluation) is an AI evaluation whose output is validated by code before it is used.

## Data flow

```
request ──> spec.json ──> plan (cost estimate, no network)
                              │ approval + --max-usd cap
                              ▼
                      discover (Apify search) ──> raw/*.json
                              ▼
                      triage: dedupe, exclude seen accounts, rank by evidence of analysis ──> enrich_plan.json
                              │ review + approval + --max-usd cap
                              ▼
                      enrich (Apify profiles) ──> raw/*.json, profile cache
                              ▼
                      prepare: pipeline (load, merge, metrics, signals, rule screen) ──> packets/*.json + RUBRIC.md
                              ▼
                      evaluation in the Claude Code session ──> evaluations/<handle>.json
                              ▼
                      export: validate (schema, citations, packet hash), tier, rank ──> CSV, evidence JSON, summary
                              ▼
                      combine (optional): merge runs ──> PRIMARY / ALTERNATE shortlist
```

## Modules

| Module | Responsibility |
|---|---|
| `workflow/spec.py` | Validates the natural-language request converted to JSON. No audience threshold is defaulted. |
| `workflow/apify_rest.py` | Stdlib REST client: actor price lookup, cost estimate, approval gate, capped run, final usage re-read. GET requests are retried; POST is never retried; interrupted runs are logged as billing-unknown. |
| `workflow/steps.py` | Query building, seen-account exclusion, triage, profile cache, evidence packets, evaluation import, tiering, draft validation, export. |
| `workflow/cli.py` | `python -m gtm_prospecting.workflow <command>`: init, plan, discover, triage, enrich, ingest, prepare, export, combine. |
| `workflow/combine.py` | Merges qualified rows from several runs; no new scoring. |
| `load.py`, `merge.py` | Read the X scraper export schema, normalise, dedupe by post and account. |
| `metrics.py` | Descriptive engagement statistics (never used as expertise evidence). |
| `signals.py` | Keyword and shape signals: topical relevance, promotion markers, analysis-evidence score. |
| `classify.py` | Rule-based preliminary status, audience tier, outreach suitability. |
| `evidence.py` | Evidence records and the manual verified-evidence schema. |
| `semantic_llm.py` | Rubric, structured evaluation schema, citation enforcement, optional API pilot runner. |
| `pilot.py` | Optional, opt-in, spend-capped API pilot (dry run by default). |

## Reusable versus crypto-specific

**Reusable as is:** request spec validation; cost estimation and approval gating; capped-run safeguards; seen-account exclusion and caching; blind evidence-packet construction; evaluation-record validation (schema, citation, stale-packet checks); tiering and outreach-readiness rules; draft validation; CSV/evidence export; multi-run combine.

**Specific to the crypto-on-X implementation:** the X scraper export schema and actor input; the keyword lists and categories in `signals.py`, `classify.py` and `semantic_llm.py`; the rubric wording; the outreach tone rules in the Skill.

**Not yet configurable:** the rubric, categories and keyword lists live in code. Making them an external configuration is a natural next step but has not been done, and no non-crypto ICP has been tested.

## Design principles

- Followers, badges and engagement are context, not evidence. The evaluator never sees them.
- Every positive judgment cites inputs; uncited positives are downgraded automatically.
- "Insufficient evidence" is distinct from "no".
- Expertise, audience, promotional risk and outreach priority are separate fields.
- No send path exists. Drafts are files with `DRAFT_NOT_SENT`.
- Every paid action needs explicit approval and a cap.
