"""Read Apify X Tweet Scraper exports (flat 41-column schema) and normalise types."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path

REQUIRED = {
    "tweet_id", "tweet_url", "text", "created_at", "like_count", "retweet_count",
    "reply_count", "quote_count", "view_count", "author_id", "author_username",
    "source_input_type", "source_input_value", "scraped_at",
}
INT_FIELDS = ("like_count", "retweet_count", "reply_count", "quote_count",
              "bookmark_count", "view_count", "author_followers")
LIST_FIELDS = ("hashtags", "mentions", "urls", "media_urls")


class SchemaError(ValueError):
    pass


def parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def split_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    return [x for x in re.split(r"\s*[|,]\s*|\s+", str(v).strip()) if x]


def _int(v):
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


def normalise(r: dict, filename: str) -> dict:
    r = dict(r)
    r["_file"] = filename
    for f in INT_FIELDS:
        r[f] = _int(r.get(f))
    for f in LIST_FIELDS:
        r[f] = split_list(r.get(f))
    r["tweet_id"] = str(r["tweet_id"])
    r["author_id"] = str(r["author_id"])
    r["author_description"] = (r.get("author_description") or "").strip() or None
    return r


def load_raw(raw_dir) -> list[dict]:
    files = sorted(Path(raw_dir).glob("*.json"))
    if not files:
        raise SchemaError(f"no .json files in {raw_dir}")
    rows = []
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SchemaError(f"{p.name}: expected a JSON list of records")
        for i, r in enumerate(data):
            missing = REQUIRED - set(r)
            if missing:
                raise SchemaError(f"{p.name}[{i}] missing fields: {sorted(missing)}")
            rows.append(normalise(r, p.name))
    return rows
