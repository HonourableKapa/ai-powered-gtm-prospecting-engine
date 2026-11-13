# AI-Powered GTM Prospecting Engine

An AI-powered, configurable GTM prospect discovery and qualification engine that automates sourcing, enrichment, evidence-based evaluation, prioritization and personalized outreach.

This is an independent GTM Engineering portfolio project. **Crypto expert discovery on X is the first and only use case implemented and tested so far.** The architecture is organised so other ICPs and sources can be added, but nothing outside crypto-on-X has been built or validated (see [What is and isn't proven](#what-is-and-isnt-proven)).

> All example data in this repository is **synthetic**: fictional people, handles, posts and numbers. No real prospect data is included.

---

## What it does

Given a plain-English request such as *"Find me 30 credible DeFi researchers on X, active in the last 30 days, and export a ranked CSV with evidence and outreach drafts"*, the engine:

1. **Discovers** candidate accounts from configured sources (X keyword search through Apify).
2. **Enriches** the most promising ones (profile bio and recent posts), reusing a local cache so repeat accounts are not paid for twice.
3. **Qualifies** each account against an ICP using an evidence-based rubric. A YES or `strong` rating must cite specific posts, and code automatically downgrades any that do not.
4. **Scores and prioritizes** with kept-separate dimensions: expertise evidence, confidence, promotional risk, audience size and outreach fit.
5. **Personalizes** short outreach drafts that Claude grounds in what the person actually published, then validates them in code (see the workflow notes for what is and is not enforced).
6. **Exports** a structured prospect database with the qualification reason, supporting post URLs, external-research URLs, engagement metrics and a draft status.

**Nothing is ever sent.** Outreach is produced as drafts marked `DRAFT_NOT_SENT`.

### The problem it addresses

Prospecting lists built from follower counts and keyword matches surface promoters and aggregators, not genuine specialists. This project treats follower count, verification badges and engagement as *context*, never as evidence of expertise, and requires cited evidence before anyone is shortlisted. It also keeps every paid step behind an explicit approval and cost cap.

---

## Architecture

Six functional layers. The table says what each layer does, where it lives, and how reusable it is today.

| Layer | What it does | Code | Reusability today |
|---|---|---|---|
| **1. Discovery** | Turns a request into a validated spec and search queries; runs a capped, approved Apify search; excludes accounts seen in earlier runs | `workflow/spec.py`, `workflow/apify_rest.py`, `workflow/steps.py` (`build_queries`, `triage`) | Spec validation, cost estimation, approval gating and seen-account exclusion are source-agnostic. The Apify client and query syntax target one X scraper actor. |
| **2. Enrichment** | Loads Apify exports, dedupes, merges profile and post data, caches profiles | `load.py`, `merge.py`, `metrics.py`, `workflow/steps.py` (cache) | Tied to the X scraper's export schema. A new source needs a new loader that emits the same internal account record. |
| **3. Qualification** | Rule-based screening, then an AI evaluation against a rubric with schema, citation and stale-evidence validation | `signals.py`, `classify.py`, `semantic_llm.py`, `evidence.py`, `workflow/steps.py` (`import_evaluations`, `assign_tier`) | The *mechanism* (blind evidence packets, structured record, citation enforcement, tiering) is reusable. The keyword lists, categories and rubric wording are crypto-specific. |
| **4. Scoring and prioritization** | Qualification tier, confidence, promotional risk, audience tier and outreach priority, each reported separately | `classify.py`, `workflow/steps.py` (`build_rows`) | Structure reusable; thresholds and signals are tuned for X and crypto. |
| **5. Personalization** | Claude writes drafts grounded in 1-2 of the person's own posts, following the Skill's writing rules; code then validates each draft | `.claude/skills/find-crypto-experts/SKILL.md` (writing rules, instructions to Claude), `workflow/steps.py` (`import_drafts`, validation) | The code validation (status must be `DRAFT_NOT_SENT`, referenced URLs must come from the evidence, qualified prospects only, unresolved placeholders flagged) is reusable. The writing and tone rules are instructions, not enforced by code, and are written for X DMs. |
| **6. Export** | `ranked_prospects.csv` (48 columns), per-prospect evidence JSON, run summary, and a multi-run combine step | `workflow/steps.py` (`export`), `workflow/combine.py` | Reusable. |

```
request (plain English)
   -> spec.json (validated)            -> plan + cost estimate  -- approval -->
   Apify discovery (capped)            -> triage (evidence-of-analysis hint) -- approval -->
   Apify profile enrichment (capped)   -> blind evidence packets
   -> evaluation in the Claude Code session (cited, validated) -> tiers
   -> drafts (DRAFT_NOT_SENT)          -> CSV + evidence JSON + summary
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Technology stack and the role of each part

| Component | Role | Why |
|---|---|---|
| **Apify** (third-party service) and the community actor `coregent/x-tweet-scraper` | Collects X posts and profiles | Managed scraping with pay-per-result pricing and a server-side spend cap (`maxTotalChargeUsd`), so the engine does not run its own scraping infrastructure and cost has a hard upper bound. |
| **Python** (standard library plus `python-dotenv`) | Ingestion, dedupe, metrics, rule-based screening, validation, tiering, export | Deterministic, testable, offline-runnable processing. Everything that can be code is code, so the AI step is the only judgment call. |
| **Claude Code** (Anthropic's CLI) | Hosts the `/find-crypto-experts` Skill and performs the evidence evaluation inside your own session | Evaluation runs in your own Claude Code session, so it needs no separate API key and makes no API calls; it does not start nested Claude processes. Records it writes are validated by code before use. |
| **Anthropic API** (optional) | An opt-in, spend-capped pilot (`python -m gtm_prospecting.pilot`) that runs the same blind rubric through the API | Kept as an alternative path. It is off by default and never runs unless you pass explicit flags. |
| **Apify MCP server** (optional, `.mcp.json`) | Lets Claude Code explore actors interactively | Convenience only; bulk data goes through the token-based REST path. |

Apify, the actor, Claude Code and the Anthropic models are third-party products. This repository contains only the orchestration, validation and qualification code that integrates with them.

---

## Workflow in detail

1. **Discovery and sourcing.** The Skill converts your request into a spec (field, count, activity window, optional region and audience criteria, keywords). `plan` prints the queries and an upper-bound cost with no network call. `discover` refuses to run without `--approve --max-usd`. The spec supports optional `exclude_terms` and `exclude_handles`, which are appended to each query as `-term` and `-from:handle` operators (with a query-length guard); the Skill does not set them automatically, so add them to your request spec when you want them. Separately, accounts already seen in earlier runs or in the profile cache are excluded automatically at the triage and prepare steps.
2. **Profile and post enrichment.** `triage` dedupes accounts and ranks who to enrich first by *evidence of analysis* in their posts (numbers, first-person method, cited sources, investigation) minus the shape of templated promotion. This is a hint, not a verdict: the Skill has Claude read the top discovery posts and edit the shortlist before any paid enrichment. `enrich` then fetches bios and recent posts, again capped and approved.
3. **Evidence-based qualification.** `prepare` writes blind evidence packets that deliberately contain no follower, badge or engagement data. Claude evaluates each packet against `RUBRIC.md`, reading full post text and, where it can change the decision, checking public sources read-only. Each record is expected to cite post URLs from the packet and state what it checked (`external_checks`). `export` validates the schema, rejects cited URLs that are not in the packet, rejects stale packets, and automatically downgrades any YES or `strong` rating that has no cited post.
4. **Scoring and prioritization.** Expertise tier (`A_QUALIFIED` to `E_INSUFFICIENT_EVIDENCE`), confidence, audience tier and outreach priority are separate columns. An `INSUFFICIENT_EVIDENCE` result is not a rejection; a small audience never lowers the expertise decision.
5. **Engagement and promotional-risk assessment.** Median views, engagement-per-view and flags are descriptive. Promotional concentration is rated separately by the evaluator and by keyword rules, and conflicts between them are kept visible.
6. **Personalized outreach generation.** The Skill instructs Claude to ground each draft in one or two of the person's own posts, avoid claims that are not in the evidence, and avoid mentioning follower counts; those are writing instructions and are not checked by code. Code does enforce that a draft is marked `DRAFT_NOT_SENT`, that its referenced URLs come from the packet, that drafts exist only for qualified prospects (the review tier only if you opt in), and that unresolved placeholders are flagged. Sender identity and value proposition are placeholders (`[[SENDER_NAME]]`, `[[VALUE_PROP]]`) until you provide them.
7. **Structured CSV export.** `export` writes `ranked_prospects.csv` (48 columns, including tier, confidence, promotional risk, audience tier, outreach readiness and draft status), one evidence JSON per prospect and `run_summary.md`. `combine` merges the qualified rows of several runs into one ranked shortlist (`combined_shortlist.csv`) with PRIMARY and ALTERNATE roles and a spot-check flag for low-confidence or high-promotion prospects.

---

## Demonstrated implementation: crypto experts on X

Request (natural language): *find N credible DeFi / onchain research experts on X, active in the last 30 days.*

Input: a spec with keywords such as "onchain analysis", "liquidation", "protocol risk". Output: a ranked table where each row explains **why** the person qualifies, links the posts it relies on, and carries a draft status.

Qualification is evidence-gated. A prospect becomes `A_QUALIFIED` only when the evaluation says YES **and** cites posts that exist in its input, and the label means *AI-assessed with validated citations, pending human verification*. Low-confidence results are flagged as the spot-check set.

### Synthetic sample output

All rows below are fictional. [examples/output/synthetic_example_prospects.csv](examples/output/synthetic_example_prospects.csv) is a hand-written illustration of a compact 21-column presentation layout. It is **not** the engine's native export and was not produced by running the pipeline: the engine's `export` step writes the 48-column `ranked_prospects.csv`, and the compact layout is a curated view over those fields. The script that assembles such a view for a real run is not part of this repository.

| Rank | Role | Name | Category | Confidence | Promo risk | Outreach status |
|---|---|---|---|---|---|---|
| 1 | PRIMARY | Chain Lens (synthetic) | onchain_analyst | high | low | DRAFT_NOT_SENT |
| 2 | PRIMARY | Quant Curve (synthetic) | investor_or_market_commentator | medium | none | DRAFT_NOT_SENT |
| 3 | ALTERNATE | New Builder (synthetic) | builder_or_developer | low | none | ON_HOLD_LOW_CONFIDENCE |

Example qualification reason (fictional): *"Publishes original liquidation analysis with sample sizes, windows and a query link, and revises a result when it proves sensitive to one outlier block. Not yet verified: reproduce one result from the posted query."*

Example draft (fictional): *"Hey, read your liquidation breakdown, especially the part where the delay figure moved from 41 to 29 seconds once you reran it. Rare to see the correction posted next to the original. Do you keep the queries public for all your analyses?"*

Columns of the illustrative compact layout: Rank, Role, Name, X handle, X profile URL, Bio, Expertise category, Qualification status, Qualification reason, Supporting X post URLs, External research URLs, Follower count, Engagement metrics, Recent activity, Originality assessment, Promotional risk, Confidence level, Outreach priority, Personalized outreach opening, Full personalized outreach message, Outreach status.

---

## Installation and usage

Requirements: Python 3 (developed and tested on 3.12; other versions untested). Claude Code for the Skill workflow. An Apify account only for live discovery.

```bash
git clone <your-fork-or-copy-of-this-repo>
cd ai-powered-gtm-prospecting-engine
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests                   # offline, no network, no keys
```

### Try the pipeline offline on synthetic data

```bash
python -m gtm_prospecting --raw examples/synthetic_data/raw --evidence config/manual_evidence.example.json --out out
```

This runs the deterministic screening on five fictional accounts and writes `out/report.csv`. It makes no network calls.

### Run the Skill (live use)

1. Copy `.env.example` to `.env` and set your own `APIFY_TOKEN` (see [Secure configuration](#secure-configuration)).
2. Open the project in Claude Code and run, for example:

   `/find-crypto-experts Find me 30 credible DeFi researchers on X, active in the last 30 days. Export a ranked CSV with evidence and outreach drafts.`

3. The Skill proposes queries and a cost estimate and **asks for your approval before each paid Apify run**.
4. Review the exported CSV and the spot-check list before any outreach. Nothing is sent by the engine.

Skill definition: [.claude/skills/find-crypto-experts/SKILL.md](.claude/skills/find-crypto-experts/SKILL.md).

---

## Secure configuration

- Credentials live only in a local `.env` (git-ignored). `.env.example` contains placeholders and is the only env file that may be committed.
- `APIFY_TOKEN` is read from the environment or `.env`, sent only in an `Authorization` header (never in a URL), and never printed or logged.
- Paid runs require `--approve --max-usd <cap>`; the cap is enforced by Apify (`maxTotalChargeUsd`). Interrupted runs are logged as billing-unknown rather than assumed free.
- The Skill's shell access is limited to this package's workflow command and the unit-test command. It can also read and write files and use web fetch and web search (the latter two for read-only verification of public sources). It is instructed never to send messages, and the code contains no send path.
- Raw scrapes, caches, run folders, evidence and drafts are git-ignored because they contain personal data. Keep real prospect databases out of version control. See [docs/SECURITY.md](docs/SECURITY.md).

---

## Limitations

- **Automated processing versus AI judgment.** Dedupe, metrics, rule screening, validation, tiering and export are deterministic code. The expertise decision itself is an AI judgment made by Claude in your session. It is validated for structure and citations, not for truth.
- **Human verification is required.** `A_QUALIFIED` means "AI-assessed, pending human verification". Bios are self-reported, linked material may not have been opened, and some sources (for example login-walled posts) cannot be read. Review the flagged spot-check set before relying on a result.
- **Outreach is draft-only.** The engine generates drafts and never sends, posts or emails.
- **Triage is a hint.** In one test, the automatic top picks overlapped only partly with the accounts a human reader chose to enrich, so a review step is built into the workflow.
- **Small samples.** Many accounts have only a few posts in the window; low-confidence results are labeled as such.
- **Third-party dependencies.** Data quality, availability and pricing depend on Apify and the chosen actor. X's terms and applicable data-protection and anti-spam rules apply to how you collect and contact people; you are responsible for compliance.
- **Test coverage.** The tests included here are the ones that run without real scraped data (client safeguards, spec validation, signal functions, combine step, and a synthetic end-to-end smoke test). The author's larger regression suite runs against private real exports and is not published.

### What is and isn't proven

| Implemented and exercised | Not built |
|---|---|
| Crypto/DeFi expert discovery and qualification on X via Apify | Other industries or ICPs |
| Approval-gated, capped paid runs; profile cache; seen-account exclusion | Non-X sources (LinkedIn, company databases, web) |
| Evidence-packet evaluation with citation enforcement; tiering; CSV export | CRM integration or automated sending |
| Draft outreach with `DRAFT_NOT_SENT` status | A configurable ICP/rubric file (rubric and categories are currently in code) |

---

## Future possibilities (not implemented)

- **Other industries.** The same pattern could be pointed at, for example, SaaS decision-makers, analysts and fintech professionals, consultants, healthcare or professional-services specialists, or operations leaders. Each would need an appropriate data source, an ICP definition and its own qualification criteria and validation; none has been built or tested here.
- **Configurable ICPs and rubrics.** Moving the rubric, categories and keyword lists from code into a config file (the `config/` folder is the intended home).
- **Additional data sources** behind the same account-record interface.
- **CRM integration and workflow automation**, with human approval retained before any outreach.

---

## Repository layout

```
.claude/skills/find-crypto-experts/   Claude Code Skill (the workflow entry point)
gtm_prospecting/                      Python package: screening, evidence, rubric, workflow CLI
  workflow/                           spec, Apify client, steps (triage, packets, tiers, export), combine
config/                               example configuration (placeholders only)
examples/synthetic_data/              fictional demo exports + generator
examples/output/                      synthetic illustration of a compact CSV layout
tests/                                offline tests
docs/                                 architecture and security notes
.env.example                          placeholder only
```

## License

MIT License. See LICENSE.
