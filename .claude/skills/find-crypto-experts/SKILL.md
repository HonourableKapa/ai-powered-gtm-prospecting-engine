---
name: find-crypto-experts
description: Find, qualify and rank credible crypto experts on X from a natural-language request (specialty, how many, activity window, region, optional audience criteria). Discovers accounts and enriches profiles via Apify, screens with the project's tested Python pipeline, evaluates expertise in this Claude Code session, and exports a ranked CSV, evidence records and DRAFT_NOT_SENT outreach drafts. Never sends anything.
argument-hint: "Find me 30 credible DeFi researchers on X, active in the last 30 days, Singapore-based, audience over 5k"
disable-model-invocation: true
allowed-tools: Bash(python -X utf8 -m gtm_prospecting.workflow:*), Bash(python -X utf8 -m unittest:*), Read, Write, Glob, Grep, AskUserQuestion, WebFetch, WebSearch
---

# /find-crypto-experts

User request: $ARGUMENTS

Work from the project root (`ai-powered-gtm-prospecting-engine`). All commands are
`python -X utf8 -m gtm_prospecting.workflow <command> --run data/runs/<run-id> ...`.

## Hard rules (never relax)
1. **Never send any message, post, DM or email.** Outreach drafts are files marked `DRAFT_NOT_SENT`. Sending is out of scope; if asked, say it needs a separate, explicit step the user performs.
2. **Paid Apify runs need the user's explicit approval each time** (use AskUserQuestion with the estimated upper bound and the proposed `--max-usd` cap). `discover` and `enrich` refuse to run without `--approve --max-usd`. Re-ask for every new paid run; approval for one run never covers another.
3. **Semantic evaluation happens here, in this session.** Do not call the Anthropic API, do not run `gtm_prospecting.pilot` (the optional API pilot) unless the user explicitly asks for it, do not start nested `claude` processes, do not use subagents.
4. **Do not invent evidence.** Cite only URLs that appear in the packet. Bios are self-reported claims. Never treat followers, verification badges or engagement as expertise.
5. **Keep three dimensions separate:** expertise (tier + evaluation), audience influence (follower/view tier), outreach priority. Do not blend them in your summary.
6. **Report shortfalls honestly.** Return fewer than requested if fewer qualify, and explain why. Never pad the list.
7. Never print, log or ask for secrets. `APIFY_TOKEN` comes only from the environment.

## Step 1: turn the request into a spec
Write `data/runs/_request.json` (create the folder if needed) with these keys (all validated by code):
`request_text` (the user's words), `field` (specialty), `n` (default 30), `window_days` (default 30),
`keywords` (6-10 X search phrases you propose for the specialty; X search matches tweet text, so use phrases experts post about, e.g. "tokenomics", "onchain analysis", "protocol risk", "smart contract audit"),
`region` (only if asked: `{"terms": ["singapore","sg"], "required": false}`; X has no region filter, so this is a soft match on self-reported location and unknown locations are kept and flagged),
`audience` (**only if the user stated criteria**, keys `min_followers`, `max_followers`, `min_median_views`; never invent a threshold),
`language` (default "en"), optional `triage_multiplier` (default 3), `profile_posts` (15), `discovery_per_query` (50), `cache_ttl_days` (14),
`draft_for_review_tier` (false unless the user asks for drafts for the REVIEW tier too).
If the specialty or count is ambiguous, ask one short question first. Then:
`init --request-file data/runs/_request.json` and note the printed run dir.

## Step 2: plan and cost
Run `plan --run <dir>` (no network). Present: the queries, the upper-bound Apify cost for discovery and for enrichment, and that semantic evaluation costs no API money (it uses this session). The default price is a pessimistic assumption; if `APIFY_TOKEN` is set (check with a Bash boolean test, never print it), add `--fetch-price` for the real per-tweet price.

## Step 3: get data
- **Live (Apify):** requires `APIFY_TOKEN` in the environment (the Apify MCP server in `.mcp.json` uses browser OAuth and is useful for exploring actors, but bulk data must go to disk through the token-based REST path). If it is not set, tell the user how to set it and stop; do not ask them to paste it.
  1. Ask approval, then `discover --run <dir> --approve --max-usd <cap>` (actor `coregent/x-tweet-scraper`, keyword searches limited to the activity window).
  2. `triage --run <dir>`: dedupes accounts, applies rules, reuses cached profiles, and ranks who to enrich first by **evidence of analysis in their tweets** (numbers, first-person method, cited sources, wallet/investigation work) minus the shape of templated promotion (styled lettering, greeting templates, campaign vocabulary, product voice, digests/course recaps, third-person relays). Keyword counts are not used. `candidates.csv` shows each account's `analysis_signals` and `template_signals`. This is a hint, not a verdict: **before approving enrichment, read the top ~40 discovery tweets yourself**, drop obvious spam and add credible accounts the ranking missed (announcement-style tweets from real researchers score low), then edit `enrich_plan.json` and state why. It writes the plan of accounts still needing a paid profile scrape (`enrich_plan.json`).
  3. Ask approval again, then `enrich --run <dir> --approve --max-usd <cap>` (profile scrape: bios plus recent posts).
- **Offline/local data:** `ingest --run <dir> <exports...>` copies existing Apify-format JSON exports (e.g. `data/raw/*.json`) into the run. No Apify call.

## Step 4: prepare evidence packets
`prepare --run <dir> [--handles a b c] [--limit N]` runs the tested pipeline (dedupe, metrics, preliminary screening), reuses cached profiles, and writes `packets/<handle>.json` plus `packets/RUBRIC.md`.
Packets deliberately contain no follower, badge or engagement data.

## Step 5: evaluate in this session
1. Read `packets/RUBRIC.md` (the rules and the exact record format).
2. Evaluate packets in batches of roughly 6-8 profiles (about 25k tokens), reading **only** the packet files while judging; do not open the CSV audience columns first. Read the **full text** of every substantive post: long posts are often the research, so a truncated excerpt is not enough to reject someone.
3. **Verify read-only where it can change the decision.** Use WebFetch/WebSearch on public sources that a post relies on (an official doc or governance post it quotes, a newsletter it links, a report it cites) and compare the numbers. Never claim to have inspected something you could not open: X articles are login-walled (HTTP 402) and `t.co` shortlinks may not resolve. Record every attempt in `external_checks` (`corroborated`, `partially_corroborated`, `contradicted`, `not_found`, `inaccessible`). `inaccessible`/`not_found` are never evidence against the account; a claim contradicted by a primary source counts against credibility. Do not run paid scrapes or API calls for this.
4. Write `evaluations/<handle>.json` exactly as the rubric specifies (copy `packet_hash` from the packet). Label every post; distinguish original research (including careful analysis of third-party data) from ordinary tweets, quote commentary and repackaged promotion.
5. **Decide, don't defer.** `YES` = substantial attributable evidence in the posts (cited post URLs); original datasets, credentials and a large audience are not required. `NO` = the evidence shows the account is outside the criteria. `INSUFFICIENT_EVIDENCE` = the evidence cannot establish either way (it is not a rejection). `REVIEW` only when one specific, important uncertainty remains; name it in `missing_verification`. Rate promotional risk separately: a small audience, one-ecosystem focus, an unverified bio or occasional promotion must not lower the expertise decision. A YES or `strong` rating with no cited post is automatically downgraded.

## Step 6: drafts (qualified prospects only)
For each prospect in the qualified tier (and the REVIEW tier only if `draft_for_review_tier` is true), write `outreach_drafts/<handle>.json`:
`{"handle","status":"DRAFT_NOT_SENT","subject","body","referenced_urls":[post URLs from the packet],"grounded_in":"what it relies on"}`.
Ground each draft in 1-2 of the person's own posts, make no claim that is not in the evidence (bios and self-reported builds are not facts), and use the placeholders `[[SENDER_NAME]]` and `[[VALUE_PROP]]` unless the user has supplied them. Do not mention follower counts.

## Step 7: export and report
`export --run <dir>` validates every evaluation (schema, citations, stale-packet check) and draft, assigns tiers, ranks, and writes:
`export/ranked_prospects.csv`, `export/evidence/<handle>.json`, `export/run_summary.md`.
If evaluations are rejected, fix the record and re-run `export`.
Tell the user: how many qualified vs requested, the shortfall explanation, the top prospects (with tier, category, confidence, evidence URLs, audience tier, outreach priority), where the files are, and that nothing was sent. Ask before any further paid run (e.g. discovering more candidates to close a shortfall) and say outreach requires their separate explicit approval.

## Tiers (what "qualified" means)
`A_QUALIFIED` = valid evaluation says YES with cited posts, in window and within any stated criteria. It means *AI-assessed with validated citations, pending human verification*; a semantic result outranks the keyword-rule screen (the conflict is kept in `tier_reason`). Low-confidence YESes are the spot-check set. `B_REVIEW` = one named uncertainty remains (should be rare); `C_NEEDS_EVALUATION`; `D_NOT_QUALIFIED` = evidence shows outside the criteria; `E_INSUFFICIENT_EVIDENCE` = cannot establish either way (not a rejection; re-queue when more evidence exists); `X_EXCLUDED` (outside the requested window/region/audience).
The manual `expert_status` column from the original pipeline is kept separate and is only YES with human-verified evidence in `data/evidence/manual_evidence.json`.
