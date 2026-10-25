"""Group similar security questions without sending answers to the model."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from bogus_security_answers.db import META_API_KEY, META_MODEL, META_PROVIDER, Database
from bogus_security_answers.errors import ProviderError, ValidationError
from bogus_security_answers.generate import ANSWER_KINDS
from bogus_security_answers.limits import TEXT_MAX
from bogus_security_answers.models import Classification, QuestionGroup
from bogus_security_answers.providers import build_provider
from bogus_security_answers.text import is_canonical_key, normalize_question

# 90 treats a one-word difference such as "what" and "which" as the same question
# without folding in a looser paraphrase.
FUZZY_THRESHOLD = 90
FUZZY_MIN_LENGTH = 8

# More specific phrases come first so "maiden" wins over "mother".
KEYWORDS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("maiden",), "mothers_maiden_name", "last_name"),
    (("first pet", "pet's name", "pets name", "name of your pet", "pet"), "first_pet", "first_name"),
    (("high school", "elementary school", "school"), "first_school", "school"),
    (("street", "grew up"), "childhood_street", "street"),
    (("born", "birth", "hometown"), "birth_city", "us_city"),
    (("city",), "favorite_city", "us_city"),
    (("vehicle", "car"), "first_vehicle", "vehicle"),
    (("food", "meal"), "favorite_food", "food"),
    (("colour", "color"), "favorite_color", "color"),
    (("employer", "company"), "employer", "employer"),
    (("occupation", "job"), "occupation", "job"),
    (("mother", "mom"), "mothers_name", "first_name"),
    (("father", "dad"), "fathers_name", "first_name"),
    (("spouse", "wife", "husband"), "spouse_name", "first_name"),
    (("year",), "favorite_year", "year"),
)


def interpret_model_json(payload: dict, groups: list[QuestionGroup]) -> Classification | None:
    if not isinstance(payload, dict):
        return None
    key = payload.get("canonical_key")
    kind = payload.get("answer_kind")
    matched = payload.get("matched_existing")
    if not isinstance(key, str) or not _is_key(key):
        return None
    if not isinstance(kind, str) or kind not in ANSWER_KINDS:
        return None
    if matched is not None and not isinstance(matched, bool):
        return None
    known = {group.canonical_key: group.answer_kind for group in groups}
    if key in known:
        if matched is False:
            return None
        return Classification(key, known[key], "ai")
    if matched is True:
        return None
    return Classification(key, kind, "ai")


def keyword_classification(question: str) -> Classification | None:
    text = normalize_question(question)
    if not text:
        return None
    for phrases, key, kind in KEYWORDS:
        for phrase in phrases:
            if _phrase_in(text, phrase):
                return Classification(key, kind, "keyword")
    return None


class Classifier:
    def __init__(self, database: Database, provider=None, threshold: int = FUZZY_THRESHOLD) -> None:
        self.database = database
        self.provider = provider
        self.threshold = threshold
        self.last_error: str | None = None

    def classify(self, question: str) -> Classification | None:
        self.last_error = None
        prepared = self._prepare(question)
        cached = self.classify_cached(prepared)
        if cached is not None:
            return cached
        groups = self.database.list_groups()
        remote = self.classify_remote(question.strip(), groups)
        if remote is not None:
            return remote
        return keyword_classification(question)

    def classify_cached(self, normalized: str) -> Classification | None:
        cached = self.database.get_classification(normalized)
        if cached is not None:
            key, kind, _source = cached
            if kind in ANSWER_KINDS and _is_key(key):
                return Classification(key, kind, "cache")
        return self._fuzzy(normalized)

    def classify_remote(self, question: str, groups: list[QuestionGroup]) -> Classification | None:
        self.last_error = None
        if self.provider is None:
            return None
        try:
            raw = self.provider.classify(question, groups)
        except ProviderError as exc:
            self.last_error = str(exc)
            return None
        parsed = interpret_model_json(raw, groups)
        if parsed is None:
            self.last_error = "The AI response could not be used."
        return parsed

    def _prepare(self, question: str) -> str:
        cleaned = question.replace("\x00", "").strip()
        if not cleaned:
            raise ValidationError("Enter a security question.")
        if len(cleaned) > TEXT_MAX:
            raise ValidationError("That question is too long.")
        return normalize_question(cleaned)

    def _fuzzy(self, normalized: str) -> Classification | None:
        if len(normalized) < FUZZY_MIN_LENGTH:
            return None
        best_score = 0.0
        best: tuple[str, str] | None = None
        for question, key, kind in self.database.list_classification_questions():
            if len(question) < FUZZY_MIN_LENGTH or kind not in ANSWER_KINDS or not _is_key(key):
                continue
            score = float(fuzz.token_sort_ratio(normalized, question))
            if score > best_score:
                best_score = score
                best = (key, kind)
        if best is not None and best_score >= self.threshold:
            return Classification(best[0], best[1], "fuzzy")
        return None


def provider_for(database: Database):
    """Build the configured AI client, or None when no API key is saved."""
    provider_name = database.get_meta(META_PROVIDER) or "openrouter"
    model = database.get_meta(META_MODEL) or ""
    api_key = database.get_meta(META_API_KEY) or ""
    return build_provider(provider_name, model, api_key)


def _phrase_in(text: str, phrase: str) -> bool:
    if " " in phrase:
        return phrase in text
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _is_key(value: str) -> bool:
    return is_canonical_key(value)
