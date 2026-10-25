import re

import pytest

from bogus_security_answers.errors import UniquenessExhausted, ValidationError
from bogus_security_answers.generate import ANSWER_KINDS, MAX_ATTEMPTS, generate_unique_answer, generate_value
from bogus_security_answers.text import normalize_answer


def test_unknown_kind_is_rejected():
    with pytest.raises(ValidationError):
        generate_unique_answer("password", set())


def test_generator_avoids_an_existing_normalized_answer():
    calls = {"n": 0}

    def factory(_kind: str) -> str:
        calls["n"] += 1
        return "Boise, ID" if calls["n"] == 1 else "Reno, NV"

    answer = generate_unique_answer("us_city", {normalize_answer("Boise, ID")}, factory=factory)
    assert answer == "Reno, NV"


def test_punctuation_and_case_count_as_the_same_answer():
    def factory(_kind: str) -> str:
        return "boise id"

    with pytest.raises(UniquenessExhausted):
        generate_unique_answer("us_city", {normalize_answer("Boise, ID")}, factory=factory)


def test_generation_stops_after_the_attempt_limit():
    calls = {"n": 0}

    def factory(_kind: str) -> str:
        calls["n"] += 1
        return "Same"

    with pytest.raises(UniquenessExhausted):
        generate_unique_answer("color", {normalize_answer("Same")}, factory=factory)
    assert calls["n"] == MAX_ATTEMPTS


def test_each_allowlisted_kind_returns_a_plausible_value():
    for kind in ANSWER_KINDS:
        value = generate_value(kind)
        assert normalize_answer(value)
    city = generate_value("us_city")
    assert ", " in city
    assert len(city.rsplit(", ", 1)[1]) == 2
    assert generate_value("school").split()[-1] in {"School", "Academy"}
    assert re.fullmatch(r"\d{4}", generate_value("year"))
    assert re.fullmatch(r"\d{4} .+ .+ ", generate_value("vehicle")) or re.fullmatch(
        r"\d{4} .+", generate_value("vehicle")
    )
