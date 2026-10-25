"""Normalization used for uniqueness, search, and question grouping."""

from __future__ import annotations

import re
import unicodedata

_ANSWER_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_CANONICAL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}(/[A-Za-z0-9._-]{1,64})?$")


def normalize_answer(text: str) -> str:
    """Fold case and punctuation so 'Boise' and 'Boise, ID' collide."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    cleaned = _ANSWER_PUNCT.sub(" ", folded)
    return _WHITESPACE.sub(" ", cleaned).strip()


def normalize_question(text: str) -> str:
    """Fold a question for cache lookup without changing its words."""
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    folded = folded.rstrip("?").strip()
    return _WHITESPACE.sub(" ", folded)


def slugify_question(text: str) -> str:
    """Build a snake_case group id from question text."""
    slug = re.sub(r"[^a-z0-9]+", "_", normalize_question(text)).strip("_")
    slug = slug[:64].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"q_{slug}" if slug else "question"
    return slug[:64]


def is_canonical_key(value: str) -> bool:
    return bool(_CANONICAL_KEY.fullmatch(value))


def is_model_name(value: str) -> bool:
    return bool(_MODEL_NAME.fullmatch(value))


def like_pattern(query: str) -> str:
    """Build a LIKE pattern that treats % and _ as ordinary characters."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
