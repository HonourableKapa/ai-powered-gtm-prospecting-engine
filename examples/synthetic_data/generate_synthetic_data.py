"""Generates a SYNTHETIC demo dataset in the Apify X Tweet Scraper export schema.

Every account, handle, post, number and link below is fictional. Nothing here was scraped from X, and none of it
describes a real person. The handles carry a `synth_` prefix and links use the reserved `.invalid` domain.

    python examples/synthetic_data/generate_synthetic_data.py
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "raw"
SCRAPED = "2026-06-30T12:00:00.000Z"

# (handle, display name, bio, followers, posts[(day_offset, text, views, likes)])
ACCOUNTS = [
    ("synth_chainlens", "Chain Lens (synthetic)", "Independent onchain analyst. Methods and sources in every post.", 1800, [
        (2, "I pulled all 1,204 liquidations on the lending market between day 10 and day 24 and compared them with the oracle feed. "
            "Median delay was 41 seconds and 83% of bad debt came from just 3 wallets. Data and query: https://data.example.invalid/q/123", 2400, 61),
        (5, "Re-ran last week's analysis with a different window. The concentration result holds (3 wallets, 80%+ of shortfall), "
            "but the median delay drops to 29 seconds. My earlier figure was sensitive to one outlier block.", 1900, 44),
        (9, "Traced the treasury outflow step by step: 4 transactions, 2 intermediate addresses, funds ended in one exchange deposit. "
            "I checked each hop against the explorer and posted the hashes in the thread.", 3100, 88),
        (13, "Fee revenue vs emissions for the protocol, quarter by quarter. I calculated net of incentives: positive in 2 of 4 quarters. "
             "Source: the protocol's own dashboard plus my own export.", 1500, 37),
        (18, "Short note on method: I always report the sample size and what I excluded. Excluded 6 contracts that were upgraded mid-window.", 900, 25),
        (22, "Writeup of how the collateral factor change affects liquidation thresholds, with a worked example using 3 positions.", 1700, 40),
    ]),
    ("synth_yieldmax", "YieldMax Official (synthetic)", "100x gems daily. Join the VIP group. Not financial advice. DM for promo.", 48000, [
        (1, "GM FRENS!!! NEW GEM ALERT 100X POTENTIAL. Use my referral link https://ref.example.invalid/a1 and join the airdrop NOW", 52000, 310),
        (3, "NEXT 1000X TOKEN IS HERE. LIMITED SPOTS. Join VIP https://vip.example.invalid/join", 61000, 280),
        (4, "Happy Friday friends! Like, retweet and follow for a chance to win. Use code YIELDMAX for bonus rewards", 43000, 400),
        (7, "Partnered post: our sponsor's new vault pays 900% APY. Sign up through the link in bio", 38000, 150),
        (11, "ALPHA THREAD: top 10 gems for the week. Link in bio. Not financial advice", 47000, 220),
    ]),
    ("synth_quantcurve", "Quant Curve (synthetic)", "Quantitative researcher. Perp funding, volatility, cross-sectional factors.", 620, [
        (3, "Sorted 40 perpetual contracts into quintiles by 30-day rolling volatility, equal weight, rebalanced weekly. "
            "Top quintile minus bottom quintile returned -4.2% over the window; I'd treat that as noise at this sample size.", 800, 22),
        (8, "Funding rate dispersion across venues: I measured the daily spread for 12 pairs over 60 days. Mean absolute spread 0.011%, "
            "with two spikes that coincide with exchange maintenance windows.", 650, 18),
        (14, "Robustness check on last week's study: dropped the 5 most illiquid contracts and the sign of the result flips. "
             "Flagging this as a limitation of my earlier post, not a finding.", 540, 15),
        (20, "Backtest note: transaction costs assumed at 6 bps per side. Results shrink by about a third once costs are included.", 480, 12),
    ]),
    ("synth_alpha_digest", "Alpha Digest Daily (synthetic)", "Your daily crypto news recap. Follow for updates. Ads: DM.", 22000, [
        (1, "Daily recap: BTC up 2%, ETH flat, three new token launches, and a governance vote passed. Read more in today's newsletter.", 9000, 40),
        (2, "Day 12 | Weekly digest: top 5 headlines in DeFi and what they mean. Subscribe for the full issue.", 8500, 35),
        (5, "BREAKING: exchange lists new token. Thread with the key points from the announcement.", 12000, 90),
        (9, "Recap of today's market moves. Sponsored by an exchange partner. Sign up with our link.", 7000, 28),
    ]),
    ("synth_newbuilder", "New Builder (synthetic)", "Solidity dev learning in public. Building a small vault monitor.", 40, [
        (4, "Shipped v0.2 of my vault monitor: it polls 5 vaults and alerts when share price drops more than 2% in an hour. "
            "Code: https://code.example.invalid/newbuilder/vault-monitor", 120, 6),
        (10, "Wrote tests for the alert logic: 14 cases, found a rounding bug when share price is below 1. Fixed in the repo.", 90, 4),
        (17, "Reading through an audit report for a lending protocol and noting how the findings were rated. Learned a lot.", 60, 3),
    ]),
]


def row(handle, name, bio, followers, idx, day, text, views, likes, source_type, source_value, uid):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc) - timedelta(days=day)
    tid = str(9000000000000000000 + uid * 100 + idx)
    return {
        "tweet_id": tid, "tweet_url": f"https://x.com/{handle}/status/{tid}", "text": text,
        "created_at": base.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "lang": "en", "like_count": likes, "retweet_count": likes // 5,
        "reply_count": likes // 8, "quote_count": likes // 20, "bookmark_count": likes // 4, "view_count": views,
        "is_reply": False, "in_reply_to_tweet_id": None, "is_retweet": False, "is_quote": False, "conversation_id": tid,
        "hashtags": "", "mentions": "", "urls": "", "media_type": None, "media_urls": "", "video_url": None, "is_sensitive": False,
        "source_app": None, "quoted_tweet_id": None, "quoted_tweet_text": None, "quoted_author_username": None,
        "retweeted_tweet_id": None, "retweeted_author_username": None,
        "author_id": str(8000000000000 + uid), "author_username": handle, "author_name": name, "author_followers": followers,
        "author_verified": False, "author_location": None, "author_description": bio if source_type == "profile" else None,
        "author_created_at": "2021-01-01T00:00:00.000Z", "author_avatar_url": None,
        "source_input_type": source_type, "source_input_value": source_value, "scraped_at": SCRAPED,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    search, profile = [], []
    for uid, (h, name, bio, fol, posts) in enumerate(ACCOUNTS, 1):
        for i, (day, text, views, likes) in enumerate(posts):
            if i < 2:   # the first two posts of each account also surface through a keyword search
                search.append(row(h, name, bio, fol, i, day, text, views, likes, "search", "onchain analysis", uid))
            profile.append(row(h, name, bio, fol, i, day, text, views, likes, "profile", h, uid))
    (OUT / "synthetic_search_export.json").write_text(json.dumps(search, indent=1), encoding="utf-8")
    (OUT / "synthetic_profile_export.json").write_text(json.dumps(profile, indent=1), encoding="utf-8")
    print(f"wrote {len(search)} search rows and {len(profile)} profile rows for {len(ACCOUNTS)} fictional accounts to {OUT}")


if __name__ == "__main__":
    main()
