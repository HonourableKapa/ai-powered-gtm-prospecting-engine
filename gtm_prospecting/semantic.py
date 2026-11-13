"""Extension point for semantic (e.g. LLM) evaluation. NOT implemented or enabled in v1.

A SemanticEvaluator returns extra EvidenceItems and per-dimension notes. The classifier labels
anything it produces as `semantic_evaluator`, and it can never by itself produce YES: YES needs a
manually verified evidence record. Wiring in an LLM later = implement `evaluate`, pass the instance
to `classify(..., semantic_evaluators=[...])`. Adding a paid service requires explicit approval.
"""
from __future__ import annotations
from typing import Protocol
from .evidence import EvidenceItem


class SemanticEvaluator(Protocol):
    name: str

    def evaluate(self, account: dict, signals: dict) -> tuple[list[EvidenceItem], dict]:
        """Return (evidence_items, dimension_notes)."""
        ...


class LLMEvaluator:
    name = "llm_semantic_evaluator"

    def evaluate(self, account, signals):
        raise NotImplementedError("LLM evaluation is intentionally not built in v1")
