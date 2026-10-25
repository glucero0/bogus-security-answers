import json

import httpx
import pytest

from bogus_security_answers.classify import Classifier, interpret_model_json, keyword_classification
from bogus_security_answers.db import Database
from bogus_security_answers.errors import ProviderError
from bogus_security_answers.models import QuestionDraft, QuestionGroup
from bogus_security_answers.providers import (
    GEMINI_MODELS_URL,
    OPENROUTER_URL,
    GeminiProvider,
    OpenRouterProvider,
    build_prompt,
    parse_json_content,
)


class FakeProvider:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def classify(self, question, groups):
        self.calls.append(question)
        if self.error is not None:
            raise self.error
        return self.response


def test_cache_does_not_call_the_provider():
    database = Database.create_empty()
    database.save_classification("what city were you born in", "birth_city", "us_city", "ai")
    provider = FakeProvider(response={"canonical_key": "other", "answer_kind": "job", "matched_existing": False})
    result = Classifier(database, provider).classify("What city were you born in?")
    assert result is not None
    assert result.source == "cache"
    assert result.canonical_key == "birth_city"
    assert provider.calls == []


def test_similar_question_reuses_the_cached_group():
    database = Database.create_empty()
    database.save_classification("what city were you born in", "birth_city", "us_city", "ai")
    provider = FakeProvider()
    result = Classifier(database, provider).classify("Which city were you born in?")
    assert result is not None
    assert result.source == "fuzzy"
    assert result.canonical_key == "birth_city"
    assert provider.calls == []


def test_provider_response_is_used_when_nothing_local_matches():
    database = Database.create_empty()
    provider = FakeProvider(
        response={"canonical_key": "first_pet", "answer_kind": "first_name", "matched_existing": False}
    )
    result = Classifier(database, provider).classify("What was the name of your first pet?")
    assert result is not None
    assert result.source == "ai"
    assert result.canonical_key == "first_pet"
    assert provider.calls == ["What was the name of your first pet?"]


def test_bad_model_kind_falls_back_to_keywords():
    database = Database.create_empty()
    provider = FakeProvider(
        response={"canonical_key": "first_pet", "answer_kind": "password", "matched_existing": False}
    )
    result = Classifier(database, provider).classify("What was your first pet?")
    assert result is not None
    assert result.source == "keyword"
    assert result.answer_kind == "first_name"


def test_provider_error_uses_keywords():
    database = Database.create_empty()
    provider = FakeProvider(error=ProviderError("down"))
    result = Classifier(database, provider).classify("What city were you born in?")
    assert result is not None
    assert result.source == "keyword"
    assert result.canonical_key == "birth_city"
    assert result.answer_kind == "us_city"


def test_unknown_matched_group_is_rejected():
    groups = [QuestionGroup("birth_city", "us_city", ("city of birth",))]
    assert (
        interpret_model_json(
            {"canonical_key": "not_a_group", "answer_kind": "us_city", "matched_existing": True},
            groups,
        )
        is None
    )
    parsed = interpret_model_json(
        {"canonical_key": "birth_city", "answer_kind": "last_name", "matched_existing": True},
        groups,
    )
    assert parsed is not None
    assert parsed.answer_kind == "us_city"


def test_classifier_returns_none_when_the_model_invents_a_matched_group():
    database = Database.create_empty()
    provider = FakeProvider(
        response={"canonical_key": "not_a_group", "answer_kind": "job", "matched_existing": True}
    )
    result = Classifier(database, provider).classify("What is the airspeed velocity of an unladen swallow?")
    assert result is None


def test_maiden_name_keyword_wins_over_mother():
    result = keyword_classification("What is your mother's maiden name?")
    assert result is not None
    assert result.canonical_key == "mothers_maiden_name"
    assert result.answer_kind == "last_name"


def test_prompt_contains_questions_and_not_answers():
    groups = [QuestionGroup("birth_city", "us_city", ("What city were you born in?",))]
    prompt = build_prompt("Mother's maiden name?", groups)
    assert "Boise" not in prompt
    assert "birth_city" in prompt
    assert "Mother's maiden name?" in prompt
    assert "us_city" in prompt


def test_fenced_json_is_parsed():
    payload = parse_json_content('```json\n{"canonical_key": "birth_city"}\n```')
    assert payload["canonical_key"] == "birth_city"


def test_openrouter_sends_the_key_in_a_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        body = json.loads(request.content.decode("utf-8"))
        seen["body"] = body
        content = '{"canonical_key":"birth_city","answer_kind":"us_city","matched_existing":false}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider("secret-key", "google/gemini-2.5-flash", client=client)
    result = provider.classify("Where were you born?", [])
    assert result["canonical_key"] == "birth_city"
    assert seen["auth"] == "Bearer secret-key"
    assert "key=" not in seen["url"]
    assert seen["url"].startswith(OPENROUTER_URL)
    assert "Boise" not in seen["body"]["messages"][1]["content"]
    assert "secret-key" not in seen["body"]["messages"][1]["content"]


def test_gemini_sends_the_key_in_a_header_and_rejects_a_path():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["x-goog-api-key"]
        seen["url"] = str(request.url)
        content = '{"canonical_key":"first_pet","answer_kind":"first_name","matched_existing":false}'
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": content}]}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider("secret-key", "gemini-2.5-flash", client=client)
    result = provider.classify("First pet?", [])
    assert result["answer_kind"] == "first_name"
    assert seen["key"] == "secret-key"
    assert "key=" not in seen["url"]
    assert seen["url"].startswith("https://generativelanguage.googleapis.com/")
    with pytest.raises(ProviderError):
        GeminiProvider("secret-key", "../evil", client=client)
    with pytest.raises(ProviderError):
        GeminiProvider("secret-key", "google/gemini-2.5-flash", client=client)


def test_gemini_connection_check_uses_the_models_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == GEMINI_MODELS_URL
        assert request.headers["x-goog-api-key"] == "secret-key"
        return httpx.Response(200, json={"models": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    GeminiProvider("secret-key", "gemini-2.5-flash", client=client).test_connection()


def test_saved_group_is_visible_to_the_classifier():
    database = Database.create_empty()
    database.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city")],
    )
    groups = database.list_groups()
    assert groups[0].canonical_key == "birth_city"
    assert "City of birth" in groups[0].examples
