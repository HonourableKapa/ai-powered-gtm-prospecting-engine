# Security and data handling

## Credentials

- Put your own `APIFY_TOKEN` in a local `.env` file (copy `.env.example`). `.env` is git-ignored; never commit it.
- The token is read from the environment (which takes precedence) or from `.env`, sent in an `Authorization` header only, and never placed in URLs, logs or output.
- The optional Anthropic API pilot reads `ANTHROPIC_API_KEY` from the environment only if you run it with explicit flags. The default workflow uses your Claude Code session and needs no API key.
- If a token is ever exposed (pasted into a chat, committed, or logged), rotate it in the provider console immediately. Deleting it from the latest commit does not remove it from Git history.

## Skill permissions

The Skill's shell access is limited to this package's workflow command and the unit-test command. It can also read and write files and use web fetch and web search for read-only checks of public sources. It is instructed never to send messages, and the code contains no send path.

## Spend controls

- `discover` and `enrich` refuse to run without `--approve --max-usd <cap>`.
- The cap is enforced by Apify (`maxTotalChargeUsd`), not only by this code.
- Plans and estimates make no network call unless you ask for the live price.

## Personal data

Scraped posts, profiles, evidence files and outreach drafts describe real people. They are excluded by `.gitignore` (`data/`, `out/`, run folders, caches, drafts, exports, `*_FINAL.csv`) and must not be published. Only the synthetic files in `examples/` are intended for version control.

You are responsible for complying with X's terms, Apify's terms, and applicable data-protection and anti-spam rules when collecting data and contacting people.

## Outreach

The engine has no send path. Drafts are marked `DRAFT_NOT_SENT` and contain placeholders for sender identity and value proposition.

## Before publishing a fork

1. Run `python -m unittest discover -s tests`; it includes checks for credential-shaped strings and private terms.
2. Confirm `git status` shows no `.env`, `data/`, `out/` or `*_FINAL.csv`.
3. Search your history for secrets before the first push (for example with a secret scanner) and rotate anything found.
