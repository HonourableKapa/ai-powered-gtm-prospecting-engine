"""Deduplicate posts by tweet_id and accounts by author_id; keep provenance of every source."""
from __future__ import annotations
from collections import defaultdict
from .load import parse_dt

POST_KEEP = ("tweet_id", "tweet_url", "text", "created_at", "lang", "like_count", "retweet_count",
             "reply_count", "quote_count", "bookmark_count", "view_count", "is_reply", "is_retweet",
             "is_quote", "hashtags", "mentions", "urls", "media_type", "quoted_author_username",
             "author_id", "author_username", "scraped_at", "conversation_id", "in_reply_to_tweet_id",
             "is_sensitive", "media_urls", "video_url", "source_app", "quoted_tweet_id", "quoted_tweet_text",
             "retweeted_tweet_id", "retweeted_author_username")


def _src(r):
    return f"{r['source_input_type']}:{r['source_input_value']}"


def dedupe_posts(rows):
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["tweet_id"]].append(r)
    posts = []
    for tid, grp in by_id.items():
        grp.sort(key=lambda r: r["scraped_at"] or "")
        latest = grp[-1]  # most recent scrape gives freshest metrics
        p = {k: latest.get(k) for k in POST_KEEP}
        p["search_sources"] = sorted({_src(r) for r in grp})
        p["source_files"] = sorted({r["_file"] for r in grp})
        p["duplicate_count"] = len(grp)
        # keep metric disagreement visible instead of silently dropping it
        vs = {r["view_count"] for r in grp}
        p["metrics_differ_across_scrapes"] = len(vs) > 1
        posts.append(p)
    posts.sort(key=lambda p: (p["author_id"], p["created_at"] or ""), reverse=False)
    return posts


def build_accounts(rows, posts):
    by_author = defaultdict(list)
    for r in rows:
        by_author[r["author_id"]].append(r)
    posts_by_author = defaultdict(list)
    for p in posts:
        posts_by_author[p["author_id"]].append(p)
    accounts = {}
    for aid, grp in by_author.items():
        grp.sort(key=lambda r: r["scraped_at"] or "")
        profile = [r for r in grp if r["source_input_type"] == "profile"]
        with_bio_profile = [r for r in profile if r["author_description"]]
        with_bio_any = [r for r in grp if r["author_description"]]
        # preference: profile-scrape with bio > any record with bio > profile record > latest
        if with_bio_profile:
            best, bio_src = with_bio_profile[-1], "profile_scrape"
        elif with_bio_any:
            best, bio_src = with_bio_any[-1], "search_scrape"
        else:
            best, bio_src = (profile or grp)[-1], "none"
        ap = sorted(posts_by_author[aid], key=lambda p: p["created_at"] or "")
        accounts[aid] = {
            "author_id": aid,
            "username": best["author_username"],
            "name": best.get("author_name"),
            "followers": best.get("author_followers"),
            "verified_badge": best.get("author_verified"),
            "location": best.get("author_location"),
            "bio": best["author_description"],
            "bio_source": bio_src,
            "account_created_at": best.get("author_created_at"),
            "avatar_url": best.get("author_avatar_url"),
            "has_profile_scrape": bool(profile),
            "usernames_seen": sorted({r["author_username"] for r in grp}),
            "posts": ap,
        }
    return accounts
