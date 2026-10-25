"""Records shared by the database, classifier, and CSV export."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QuestionDraft:
    question_text: str
    answer_text: str
    canonical_key: str
    answer_kind: str
    question_id: int | None = None


@dataclass(frozen=True)
class AccountSummary:
    account_id: int
    account_name: str
    updated_at: str


@dataclass(frozen=True)
class SearchRow:
    account_id: int
    account_name: str
    question_id: int | None
    question_text: str
    answer_text: str
    updated_at: str


@dataclass(frozen=True)
class QuestionGroup:
    canonical_key: str
    answer_kind: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    canonical_key: str
    answer_kind: str
    source: str
