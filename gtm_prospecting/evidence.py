"""Evidence items (what the pipeline cites) and the manually reviewed external-evidence schema."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

# origin: where the item came from. verification: how far it has been checked.
# polarity: supporting / conflicting / neutral with respect to expert qualification.
@dataclass
class EvidenceItem:
    claim: str
    polarity: str                 # supporting | conflicting | neutral
    evidence_type: str            # see EVIDENCE_TYPES
    verification: str             # self_reported | scrape_signal | manually_verified | pending | rejected
    source_url: str | None
    date: str | None
    confidence: str               # low | medium | high
    explanation: str
    origin: str = "rule_signal"   # rule_signal | manual_record | semantic_evaluator

    def to_dict(self):
        return asdict(self)


EVIDENCE_TYPES = {
    "independent_third_party",    # someone else documents/attributes the person's work or role
    "primary_work_artifact",      # the work itself: repo, paper, dashboard, audit report (attributable to them)
    "self_published_analysis",    # their own long-form/technical analysis with a source URL
    "self_reported_claim",
    "other",
}
STRENGTHS = {"substantial", "moderate", "weak"}
STATUSES = {"pending", "verified", "rejected"}
# types that can carry a YES (not only independent third-party evidence)
YES_CAPABLE = {"independent_third_party", "primary_work_artifact", "self_published_analysis"}

REQUIRED_MANUAL = {"record_id", "username", "claim", "evidence_type", "verification_status"}


class EvidenceError(ValueError):
    pass


def validate_manual(rec: dict) -> dict:
    missing = REQUIRED_MANUAL - set(rec)
    if missing:
        raise EvidenceError(f"manual evidence {rec.get('record_id')}: missing {sorted(missing)}")
    if rec["evidence_type"] not in EVIDENCE_TYPES:
        raise EvidenceError(f"{rec['record_id']}: bad evidence_type {rec['evidence_type']!r}")
    if rec["verification_status"] not in STATUSES:
        raise EvidenceError(f"{rec['record_id']}: bad verification_status {rec['verification_status']!r}")
    if rec.get("strength") not in STRENGTHS | {None}:
        raise EvidenceError(f"{rec['record_id']}: bad strength {rec.get('strength')!r}")
    if rec["verification_status"] == "verified":
        # positive claims need a source URL and a named verifier/date: never invent evidence
        for f in ("source_url", "verified_by", "verified_at", "strength"):
            if not rec.get(f):
                raise EvidenceError(f"{rec['record_id']}: verified record requires {f}")
        if not str(rec["source_url"]).startswith(("http://", "https://")):
            raise EvidenceError(f"{rec['record_id']}: source_url must be http(s)")
    return rec


def load_manual(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) else data
    out, seen = [], set()
    for r in recs:
        validate_manual(r)
        if r["record_id"] in seen:
            raise EvidenceError(f"duplicate record_id {r['record_id']}")
        seen.add(r["record_id"])
        out.append(r)
    return out


def records_for(account: dict, records: list[dict]) -> list[dict]:
    names = {u.lower() for u in account["usernames_seen"]}
    return [r for r in records
            if str(r.get("author_id") or "") == account["author_id"]
            or r["username"].lstrip("@").lower() in names]


def manual_to_item(r: dict) -> EvidenceItem:
    status = r["verification_status"]
    verification = {"verified": "manually_verified", "pending": "pending", "rejected": "rejected"}[status]
    return EvidenceItem(
        claim=r["claim"],
        polarity="neutral" if status != "verified" else "supporting",
        evidence_type=r["evidence_type"],
        verification=verification,
        source_url=r.get("source_url"),
        date=r.get("source_date"),
        confidence={"substantial": "high", "moderate": "medium", "weak": "low"}.get(r.get("strength"), "low"),
        explanation=(r.get("notes") or "") + f" [record {r['record_id']}, status={status}]",
        origin="manual_record",
    )
