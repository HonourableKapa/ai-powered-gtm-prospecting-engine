"""Deterministic PRELIMINARY signals. Keyword/regex hits are triage hints with cited posts;
they never establish expertise on their own."""
from __future__ import annotations
import re
from collections import Counter
from urllib.parse import urlparse
from .evidence import EvidenceItem
from .load import parse_dt


def _rx(words):
    return re.compile(r"(?<![\w$#@])(?:" + "|".join(words) + r")(?![\w])", re.I)


CRYPTO = _rx([r"crypto\w*", r"defi", r"on-?chain", r"token\w*", r"ethereum", r"solana", r"bitcoin", r"btc",
              r"eth", r"nfts?", r"dao", r"wallets?", r"validators?", r"smart contracts?", r"tvl", r"apy",
              r"memecoins?", r"l1", r"l2", r"staking", r"vaults?", r"perp\w*", r"web3", r"blockchain",
              r"liquidity", r"stablecoins?", r"usdc", r"mainnet", r"testnet", r"arkham", r"airdrops?",
              r"alts?", r"aave", r"trad(?:e|ing)", r"onchain"])
TECH = _rx([r"tokenomics", r"tvl", r"apy", r"validators?", r"slots?", r"latency", r"audits?", r"exploits?",
            r"vulnerabilit\w+", r"solidity", r"mev", r"liquidity", r"oracles?", r"consensus", r"zk\w*",
            r"bytecode", r"rollups?", r"fuzz\w*", r"smart contracts?", r"on-?chain data", r"dune",
            r"arkham", r"nansen", r"emissions?", r"vesting", r"unlocks?", r"funding fees?", r"perpetuals?",
            r"block propagation", r"mainnet", r"testnet", r"open interest", r"slippage"])
PROMO = _rx([r"referral", r"use code", r"dm open", r"giveaway", r"airdrop", r"leaderboard", r"campaign",
             r"waitlist", r"sponsored", r"#ad", r"partnership", r"promo\w*", r"whitelist", r"check yours",
             r"sign up", r"join", r"free for", r"first \d+ spots", r"spots?", r"fan szn", r"founding member\w*"])
BUILT = re.compile(r"\b(?:i|we)\s+(?:just\s+)?(?:built|shipped|created|launched|developed|wrote|published)\b", re.I)
BIO_PROMO = re.compile(r"dm open|promotion|marketing|kol|sponsor|paid partnership|shill|advertis|ambassador", re.I)
BIO_COMPANY = re.compile(r"\bofficial\b|\bour (?:team|mission|platform|products?)\b|\bwe(?:'re| are) (?:a|the|building)\b", re.I)
ROLE = re.compile(r"(?<![\w])(co-?founder|founder|ceo|cto|head of \w+|partner|investor|investment \w+|researcher|"
                  r"analyst|engineer|developer|auditor|security \w+|lead|strategist|trader|builder|creator|manager)(?![\w])",
                  re.I)
HANDLE = re.compile(r"@(\w{1,15})")
COLLAB = re.compile(r"dm(?:s)? (?:are )?open|open to|collab\w*|let'?s (?:talk|chat|build)|hiring|looking for|reach out|contact", re.I)
NOT_COLLECTED = ["following_count", "total_tweet_count", "bio_links/website", "pinned_post", "GitHub activity/commits",
                 "third-party mentions or independent references", "published papers/reports", "follower quality"]
GITHUB = re.compile(r"https?://(?:www\.)?github\.com/[\w.-]+(?:/[\w.-]+)?", re.I)

# ---- evidence of analysis (drives discovery triage; replaces keyword frequency) ----------------------------------
# Search queries guarantee that every discovered tweet contains crypto/DeFi keywords, so keyword counts cannot tell
# research from campaign copy. These features look for the work itself (numbers, method, sources, investigation) and
# for the shape of templated promotion. They are triage hints only: they never decide expertise.
_STYLED = re.compile("[\U0001D400-\U0001D7FF]")           # Unicode 'math bold/italic' lettering used by templated posts
_NUM = re.compile(r"(?<![\w.])(?:\$\s?\d[\d,]*(?:\.\d+)?\s?[kKmMbB]?|\d[\d,]*(?:\.\d+)?\s?(?:%|bps|[kKmMbB](?![a-zA-Z])|x(?![a-zA-Z]))"
                  r"|\d{1,3}(?:,\d{3})+|\d+\.\d+)")
_FIRST = re.compile(r"\b(?:i|my|i'm|i've|i'd|we|we've)\b", re.I)
_METHOD = re.compile(
    r"\b(?:i|we)\s+(?:just\s+)?(?:checked|looked|traced|tested|compared|scanned|went through|proof-?checked|measured|computed|"
    r"calculated|ran|pulled|found|read|analy[sz]ed|simulated|opened|copied|took|turned)\b"
    r"|\bmy (?:math|number|read|test|method)\b|\bhere'?s (?:how|what the|how i)\b|\bworked example\b|\bcontrol group\b"
    r"|\bmedian\b|\bwithout (?:his|her|the) best\b|\bslippage\b|\bbaseline\b|\bbacktest\w*", re.I)
_SOURCE = re.compile(r"\b(?:dune|defillama|etherscan|solscan|governance|ARFC|whitepaper|docs?|dashboard|report|elliptic|arkham|"
                     r"nansen|dexscreener|bubblemaps|hyperliquid|alea|llamarisk|glassnode)\b", re.I)
_INVEST = re.compile(r"\b(?:traced?|wallets?|deployer|bundl\w*)\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b|\b0x[0-9a-fA-F]{8,}\b", re.I)
_GREET = re.compile(r"^\W*(?:gm\b|good morning|happy\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|happy weekend|"
                    r"hi friend)", re.I)
_CAMPAIGN = re.compile(r"\bcampaign\b|\bleaderboard\b|\bairdrop\b|\breferral\b|\bgiveaway\b|\bwaitlist\b|\bwhitelist\b|\bstay tuned\b|"
                       r"\bcoming soon\b|\bgtm\b|\bjoin (?:the|our|us)\b|\bseason\b|\beducational\b|\bnew chapter\b", re.I)
_VOICE = re.compile(r"\bour (?:ai|platform|product|app|team|tool|analyst|dashboard|protocol|mission)\b|"
                    r"\bwe(?:'re| are) (?:building|launching|excited|proud)\b|\bintroducing\b|\bannouncing\b|\blive now\b|"
                    r"\btry (?:it|now|free)\b|\bsign up\b|\bmcp server\b|\bofficial (?:dune )?(?:dashboard|data)\b|"
                    r"\bpublic dune dashboard (?:for|of)\b|\bbacked by\b|\bsince its mainnet\b|\bhosted\b", re.I)
_DIGEST = re.compile(r"\bdigest\b|\bpower thread\b|\bweekly (?:recap|roundup)\b|\bbreaking\b|\bjust in\b|\balpha radar\b|"
                     r"\bcurriculum\b|\bday \d+\b|\bclass\b|\bassignment\b|\brecap\b|^\W{0,6}\U0001F4C1", re.I)
_RELAY = re.compile(r"\b(?:analy[sz]ed|reported|according to|claims?|counted|published|announced|snapshot identifies|"
                    r"puts approximately|linked to)\b", re.I)


def _caps_headline(text: str) -> bool:
    head = text.strip().split("\n")[0][:90]
    letters = [c for c in head if c.isalpha()]
    return len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7


def analysis_evidence(text: str) -> dict:
    """Per-post triage score: evidence of analysis minus templated-promotion shape. Higher = more worth enriching."""
    text = text or ""
    letters = sum(c.isalpha() for c in text) or 1
    person = bool(_FIRST.search(text))
    pos = {"quantitative": min(len(_NUM.findall(text)), 6) / 6, "method": bool(_METHOD.search(text)),
           "sourced": bool(_SOURCE.search(text)), "investigation": bool(_INVEST.search(text)), "first_person": person}
    styled = len(_STYLED.findall(text))
    neg = {"styled_lettering": styled >= 12 or styled / letters > 0.15, "greeting_template": bool(_GREET.search(text)),
           "campaign_vocabulary": bool(_CAMPAIGN.search(text)), "product_voice": bool(_VOICE.search(text)),
           "digest_or_course": bool(_DIGEST.search(text)), "caps_headline": _caps_headline(text),
           "relay": bool(_RELAY.search(text)) and not person}
    analysis = 2 * pos["quantitative"] + 2 * pos["method"] + pos["sourced"] + pos["investigation"] + 0.5 * person
    template = (2 * neg["styled_lettering"] + neg["greeting_template"] + neg["campaign_vocabulary"] + 1.5 * neg["product_voice"]
                + 1.5 * neg["digest_or_course"] + neg["caps_headline"] + 1.5 * neg["relay"])
    return {"score": round(analysis - 1.5 * template, 3), "analysis": round(analysis, 3), "template": round(template, 3),
            "positive": [k for k, v in pos.items() if v], "negative": [k for k, v in neg.items() if v]}


def account_type(acc) -> tuple[str, str]:
    u, n, b = acc["username"] or "", acc.get("name") or "", acc.get("bio") or ""
    if re.search(r"bot$", u, re.I) or re.search(r"\bbot\b|bot$", n, re.I):
        return "company_or_bot_likely", f"username/name looks like a bot or product ({u}/{n})"
    if re.search(r"(news|alerts?|signals)$", u, re.I) or re.search(r"\bnews\b|\balerts?\b", n, re.I):
        return "company_or_bot_likely", f"username/name looks like a news/alert feed ({u}/{n})"
    if b and BIO_COMPANY.search(b):
        return "company_or_bot_likely", "bio uses organisational language"
    if b:
        return "individual_likely", "no organisational markers in username/name/bio"
    return "unknown", "no bio available"


def _post_flags(p):
    t = p["text"] or ""
    me = (p["author_username"] or "").lower()
    tech_terms = {m.group(0).lower() for m in TECH.finditer(t)}
    return {
        "crypto": bool(CRYPTO.search(t)),
        "technical": len(tech_terms) >= 2 or bool(GITHUB.search(" ".join(p["urls"]))),
        "promo": bool(PROMO.search(t)),
        "built": bool(BUILT.search(t)),
        "mentions": [m.lower() for m in p["mentions"] if m.lower() != me],
    }


def compute(acc, as_of) -> tuple[dict, list[EvidenceItem]]:
    posts = acc["posts"]
    n = len(posts)
    flags = [_post_flags(p) for p in posts]
    items: list[EvidenceItem] = []

    def share(k):
        return round(sum(f[k] for f in flags) / n, 3) if n else 0.0

    # entity concentration: share of posts mentioning the most-mentioned other handle
    cnt = Counter(m for f in flags for m in set(f["mentions"]))
    top, top_n = (cnt.most_common(1)[0] if cnt else (None, 0))
    ent_conc = round(top_n / n, 3) if n else 0.0
    bio = acc.get("bio") or ""
    bio_promo = bool(BIO_PROMO.search(bio))
    promo_share = share("promo")
    # many posts tagging many different projects can be sponsored content even without one dominant entity
    mention_share = round(sum(bool(f["mentions"]) for f in flags) / n, 3) if n else 0.0
    tech_share = share("technical")

    if n < 3:
        risk = "UNKNOWN"
    elif ent_conc >= 0.5 or (promo_share >= 0.4 and bio_promo):
        risk = "HIGH"
    elif ent_conc >= 0.25 or promo_share >= 0.2 or bio_promo or mention_share >= 0.5:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # does concentrated entity coverage look like analysis or marketing? (hint only)
    top_tech = sum(f["technical"] for f in flags if top and top in f["mentions"])
    if top and top_n >= 3:
        hint = ("entity-focused posts contain technical terms; check whether the analysis is independent"
                if top_tech / top_n >= 0.5 else
                "entity-focused posts are mostly non-technical; reads as repeated marketing")
    else:
        hint = None

    atype, atype_reason = account_type(acc)
    dts = [parse_dt(p["created_at"]) for p in posts if p["created_at"]]
    sig = {
        "account_type": atype, "account_type_reason": atype_reason,
        "crypto_relevance_share": share("crypto"), "technical_post_share": tech_share,
        "promo_marker_share": promo_share, "project_mention_share": mention_share, "bio_promo_language": bio_promo,
        "top_mentioned_entity": top, "top_entity_post_share": ent_conc,
        "entity_coverage_hint": hint, "promo_risk": risk,
        "built_claim_posts": sum(f["built"] for f in flags),
        "active_days": len({d.date() for d in dts}),
        "days_since_last_post": (as_of - max(dts)).days if dts else None,
    }

    # --- profile / content-mix / credibility signals (all derived from fields present in the exports) ---
    own = lambda p: not (p["is_quote"] or p["is_reply"] or p["is_retweet"])
    mix = lambda pred: round(sum(pred(p) for p in posts) / n, 3) if n else None
    span_days = (max(dts) - min(dts)).days + 1 if dts else None
    domains = Counter()
    for p in posts:
        for u in p["urls"]:
            host = (urlparse(u).hostname or "").lower().removeprefix("www.")
            if host and host not in ("x.com", "twitter.com", "t.co"):
                domains[host] += 1
    gh = sorted({g for p in posts for g in GITHUB.findall(" ".join(p["urls"]))})
    bio_roles = sorted({m.group(0).lower() for m in ROLE.finditer(bio)})
    bio_affil = sorted({h for h in HANDLE.findall(bio)})
    sig.update({
        "original_post_share": mix(own), "quote_post_share": mix(lambda p: p["is_quote"]),
        "reply_post_share": mix(lambda p: p["is_reply"]), "repost_share": mix(lambda p: p["is_retweet"]),
        "media_post_share": mix(lambda p: (p.get("media_type") or "none") != "none"),
        "languages": dict(Counter(p.get("lang") or "unknown" for p in posts)),
        "sample_span_days": span_days,
        "posts_per_week_in_sample": round(n / span_days * 7, 2) if span_days else None,
        "frequency_caveat": "sample-bounded: scrapes return a limited recent slice, so this is not true posting frequency",
        "bio_roles_self_reported": bio_roles, "bio_affiliations_self_reported": bio_affil,
        "collaboration_signals": sorted({m.group(0).lower() for m in COLLAB.finditer(bio)}),
        "linked_domains": dict(domains.most_common(8)), "github_links_in_posts": gh,
        "independent_references": "unknown_not_collected", "github_activity": "unknown_not_collected",
        "published_work": "unknown_not_collected",
        "x_verified_badge": acc.get("verified_badge"),
        "verified_badge_note": "X verification is not evidence of expertise and is not scored",
    })

    # --- evidence items: each cites a source URL; self-reported stays labelled self-reported ---
    profile_url = f"https://x.com/{acc['username']}"
    if bio:
        items.append(EvidenceItem(
            claim=f"Bio (from {acc['bio_source']}): {bio!r}", polarity="neutral",
            evidence_type="self_reported_claim", verification="self_reported",
            source_url=profile_url, date=None, confidence="low",
            explanation="Self-described role; not verified. Cannot support YES by itself."))
    for role in bio_roles:
        items.append(EvidenceItem(
            claim=f"Bio self-reports role term {role!r}", polarity="neutral", evidence_type="self_reported_claim",
            verification="self_reported", source_url=profile_url, date=None, confidence="low",
            explanation="Bio text is a self-reported claim until independently verified."))
    for h in bio_affil:
        items.append(EvidenceItem(
            claim=f"Bio mentions affiliation/handle @{h} (relationship and role NOT verified)", polarity="neutral",
            evidence_type="self_reported_claim", verification="self_reported", source_url=profile_url, date=None,
            confidence="low", explanation=f"Check https://x.com/{h} and independent sources for the actual relationship."))
    for p, f in zip(posts, flags):
        if f["built"]:
            items.append(EvidenceItem(
                claim=f"Claims to have built something: {p['text'][:140]!r}", polarity="supporting",
                evidence_type="self_reported_claim", verification="self_reported",
                source_url=p["tweet_url"], date=p["created_at"], confidence="low",
                explanation="'I/we built' statement; no repository, audit or third-party record checked."))
        for g in GITHUB.findall(" ".join(p["urls"])):
            items.append(EvidenceItem(
                claim=f"Post links a GitHub URL ({g}); authorship by this account NOT verified",
                polarity="neutral", evidence_type="primary_work_artifact", verification="scrape_signal",
                source_url=p["tweet_url"], date=p["created_at"], confidence="low",
                explanation="Linking to a repo is not evidence the account wrote it."))
        if f["technical"] and not f["promo"] and len(p["text"]) > 200:
            items.append(EvidenceItem(
                claim=f"Longer post with multiple technical terms: {p['text'][:120]!r}",
                polarity="supporting", evidence_type="self_published_analysis",
                verification="scrape_signal", source_url=p["tweet_url"], date=p["created_at"],
                confidence="low", explanation="Keyword-level technical signal only; quality not assessed."))
    if risk in ("HIGH", "MEDIUM"):
        for p, f in zip(posts, flags):
            if f["promo"] or (top and top in f["mentions"]):
                items.append(EvidenceItem(
                    claim=f"Promotional/entity-focused content ({top or 'promo markers'}): {p['text'][:100]!r}",
                    polarity="conflicting", evidence_type="other", verification="scrape_signal",
                    source_url=p["tweet_url"], date=p["created_at"], confidence="low",
                    explanation="Counts toward promotional concentration (risk signal, not disqualifier)."))
    if bio_promo:
        items.append(EvidenceItem(
            claim="Bio advertises marketing/promotion services", polarity="conflicting",
            evidence_type="self_reported_claim", verification="self_reported",
            source_url=profile_url, date=None, confidence="medium",
            explanation="Self-described promotional role."))
    return sig, items
