"""Engagement-per-view metrics. Descriptive only; never an input to expertise status."""
from __future__ import annotations
from statistics import median

LOW_VIEWS_MEDIAN = 200
SMALL_SAMPLE = 10
VERY_SMALL_SAMPLE = 3
HIGH_RATE_MEDIAN = 0.05      # median (likes+replies+rts+quotes)/views above this is unusual
HIGH_RATE_POST = 0.15
DISPERSION_RATIO = 20        # max views / median views


def post_rate(p):
    v = p.get("view_count")
    if not v:
        return None
    n = sum(p.get(k) or 0 for k in ("like_count", "reply_count", "retweet_count", "quote_count"))
    return n / v


def engagement(posts) -> dict:
    rates = [r for r in (post_rate(p) for p in posts) if r is not None]
    views = [p["view_count"] for p in posts if p.get("view_count")]
    n = len(posts)
    m = {
        "n_posts": n,
        "n_posts_with_views": len(rates),
        "median_views": median(views) if views else None,
        "max_views": max(views) if views else None,
        "median_engagement_per_view": round(median(rates), 5) if rates else None,
        "max_post_engagement_per_view": round(max(rates), 5) if rates else None,
    }
    flags = []
    if n < VERY_SMALL_SAMPLE:
        flags.append("VERY_SMALL_SAMPLE")
    elif n < SMALL_SAMPLE:
        flags.append("SMALL_SAMPLE")
    if not rates:
        flags.append("NO_VIEW_DATA")
    else:
        if m["median_views"] < LOW_VIEWS_MEDIAN:
            flags.append("LOW_VIEWS")
        if m["median_engagement_per_view"] > HIGH_RATE_MEDIAN:
            flags.append("HIGH_MEDIAN_RATE")
        if m["max_post_engagement_per_view"] > HIGH_RATE_POST:
            flags.append("HIGH_RATE_POSTS")
        if m["median_views"] and m["max_views"] / m["median_views"] >= DISPERSION_RATIO:
            flags.append("VIEWS_OUTLIER_DISPERSION")
    for k, name in (("like_count", "median_likes"), ("reply_count", "median_replies"),
                    ("retweet_count", "median_reposts"), ("quote_count", "median_quotes"),
                    ("bookmark_count", "median_bookmarks")):
        vals = [p[k] for p in posts if p.get(k) is not None]
        m[name] = median(vals) if vals else None
    m["engagement_flags"] = flags
    return m
