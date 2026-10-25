"""OpenRouter and Gemini clients. Question text is sent. Answers are not."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

import httpx

from bogus_security_answers.errors import ProviderError
from bogus_security_answers.generate import ANSWER_KINDS
from bogus_security_answers.models import QuestionGroup
from bogus_security_answers.text import is_model_name

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT_SECONDS = 30.0

DEFAULT_MODELS = {
    "openrouter": "google/gemini-2.5-flash",
    "gemini": "gemini-2.5-flash",
}


def build_prompt(question: str, groups: list[QuestionGroup]) -> str:
    lines = []
    for group in groups:
        examples = "; ".join(group.examples[:3])
        lines.append(f"- {group.canonical_key} ({group.answer_kind}): {examples}")
    catalog = "\n".join(lines) if lines else "(none yet)"
    kinds = ", ".join(ANSWER_KINDS)
    return (
        "You group security questions. Reply with JSON only, no markdown.\n"
        'Schema: {"canonical_key": "snake_case", "answer_kind": "one of the kinds", '
        '"matched_existing": true or false}\n'
        f"answer_kind must be one of: {kinds}.\n"
        "If the question means the same thing as an existing group, set matched_existing "
        "to true and reuse that canonical_key.\n"
        "If it is a different question, set matched_existing to false and invent a new "
        "snake_case canonical_key.\n"
        "Do not include an answer.\n\n"
        f"Existing groups:\n{catalog}\n\n"
        f"Question: {question.strip()}\n"
    )


def parse_json_content(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text, count=1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError("The AI response was not valid JSON.")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise ProviderError("The AI response was not valid JSON.") from None
    if not isinstance(value, dict):
        raise ProviderError("The AI response was not a JSON object.")
    return value


def build_provider(provider: str, model: str, api_key: str, client: httpx.Client | None = None):
    secret = api_key.strip()
    if not secret:
        return None
    model_name = model.strip() or DEFAULT_MODELS.get(provider, "")
    if provider == "openrouter":
        return OpenRouterProvider(secret, model_name, client=client)
    if provider == "gemini":
        return GeminiProvider(secret, model_name, client=client)
    raise ProviderError("Choose OpenRouter or Gemini.")


class _JsonProvider:
    def __init__(self, api_key: str, model: str, client: httpx.Client | None) -> None:
        if not api_key.strip():
            raise ProviderError("Add an API key in Settings.")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT_SECONDS)
        return self._client


class OpenRouterProvider(_JsonProvider):
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None) -> None:
        super().__init__(api_key, model, client)
        if not is_model_name(self.model):
            raise ProviderError("The OpenRouter model name is not valid.")

    def classify(self, question: str, groups: list[QuestionGroup]) -> dict:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You reply with one JSON object and nothing else."},
                {"role": "user", "content": build_prompt(question, groups)},
            ],
            "response_format": {"type": "json_object"},
        }
        payload = self._post(OPENROUTER_URL, body)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("OpenRouter returned an unexpected response.") from None
        if not isinstance(content, str):
            raise ProviderError("OpenRouter returned an unexpected response.")
        return parse_json_content(content)

    def test_connection(self) -> None:
        try:
            response = self._http().get(
                OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError:
            raise ProviderError("Could not reach OpenRouter.") from None
        if response.status_code != 200:
            raise ProviderError(f"OpenRouter rejected the API key ({response.status_code}).")

    def _post(self, url: str, body: dict) -> dict:
        try:
            response = self._http().post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError:
            raise ProviderError("Could not reach OpenRouter.") from None
        if response.status_code != 200:
            raise ProviderError(f"OpenRouter request failed ({response.status_code}).")
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise ProviderError("OpenRouter returned an unexpected response.") from None
        if not isinstance(payload, dict):
            raise ProviderError("OpenRouter returned an unexpected response.")
        return payload


class GeminiProvider(_JsonProvider):
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None) -> None:
        super().__init__(api_key, model, client)
        if not is_model_name(self.model) or "/" in self.model:
            raise ProviderError("The Gemini model name is not valid.")

    def classify(self, question: str, groups: list[QuestionGroup]) -> dict:
        body = {
            "contents": [{"role": "user", "parts": [{"text": build_prompt(question, groups)}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        payload = self._post(self._generate_url(), body)
        try:
            content = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Gemini returned an unexpected response.") from None
        if not isinstance(content, str):
            raise ProviderError("Gemini returned an unexpected response.")
        return parse_json_content(content)

    def test_connection(self) -> None:
        try:
            response = self._http().get(
                GEMINI_MODELS_URL,
                headers={"x-goog-api-key": self.api_key},
            )
        except httpx.HTTPError:
            raise ProviderError("Could not reach Gemini.") from None
        if response.status_code != 200:
            raise ProviderError(f"Gemini rejected the API key ({response.status_code}).")

    def _generate_url(self) -> str:
        return (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent"
        )

    def _post(self, url: str, body: dict) -> dict:
        try:
            response = self._http().post(
                url,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError:
            raise ProviderError("Could not reach Gemini.") from None
        if response.status_code != 200:
            raise ProviderError(f"Gemini request failed ({response.status_code}).")
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise ProviderError("Gemini returned an unexpected response.") from None
        if not isinstance(payload, dict):
            raise ProviderError("Gemini returned an unexpected response.")
        return payload
