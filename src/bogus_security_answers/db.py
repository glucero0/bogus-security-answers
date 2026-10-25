"""In-memory SQLite database for accounts, questions, and settings."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from bogus_security_answers.errors import CollisionError, ValidationError, VaultError
from bogus_security_answers.generate import ANSWER_KINDS
from bogus_security_answers.limits import (
    API_KEY_MAX,
    IDLE_MAX_MINUTES,
    IDLE_MIN_MINUTES,
    NAME_MAX,
    TEXT_MAX,
)
from bogus_security_answers.models import AccountSummary, QuestionDraft, QuestionGroup, SearchRow
from bogus_security_answers.providers import DEFAULT_MODELS
from bogus_security_answers.text import is_canonical_key, is_model_name, like_pattern, normalize_answer, normalize_question

SCHEMA = """
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    updated_at TEXT NOT NULL
);
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    question_text TEXT NOT NULL COLLATE NOCASE,
    answer_text TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    answer_kind TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, question_text)
);
CREATE INDEX idx_questions_group ON questions (canonical_key);
CREATE TABLE classifications (
    normalized_question TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    answer_kind TEXT NOT NULL,
    source TEXT NOT NULL
);
"""

META_SCHEMA = "schema_version"
META_PROVIDER = "ai_provider"
META_MODEL = "ai_model"
META_API_KEY = "ai_api_key"
META_IDLE = "idle_timeout_minutes"
SCHEMA_VERSION = "1"
CLASSIFICATION_SOURCES = {"cache", "fuzzy", "ai", "keyword", "user", "saved"}
REQUIRED_TABLES = {"meta", "accounts", "questions", "classifications"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, connection: sqlite3.Connection, now: Callable[[], str] | None = None) -> None:
        self.connection = connection
        self._now = now or utc_now

    @classmethod
    def create_empty(cls, now: Callable[[], str] | None = None) -> Database:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        database = cls(connection, now=now)
        database._insert_defaults()
        database.commit()
        return database

    @classmethod
    def from_bytes(cls, payload: bytes, now: Callable[[], str] | None = None) -> Database:
        if len(payload) > 64 * 1024 * 1024:
            raise VaultError("The vault data could not be read.")
        connection = sqlite3.connect(":memory:")
        try:
            connection.deserialize(payload)
        except sqlite3.DatabaseError:
            connection.close()
            raise VaultError("The vault data could not be read.") from None
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        database = cls(connection, now=now)
        database._require_schema()
        return database

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def serialize(self) -> bytes:
        self.commit()
        return self.connection.serialize()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def update_settings(self, provider: str, model: str, api_key: str, idle_minutes: int) -> None:
        if provider not in DEFAULT_MODELS:
            raise ValidationError("Choose OpenRouter or Gemini.")
        model_name = model.strip()
        if not is_model_name(model_name):
            raise ValidationError("The model name contains unsupported characters.")
        if provider == "gemini" and "/" in model_name:
            raise ValidationError("A Gemini model name cannot contain a slash.")
        secret = api_key.strip()
        if len(secret) > API_KEY_MAX or "\n" in secret or "\r" in secret or "\x00" in secret:
            raise ValidationError("The API key is not valid.")
        if isinstance(idle_minutes, bool) or not isinstance(idle_minutes, int):
            raise ValidationError("Idle lock must be a whole number of minutes.")
        if not IDLE_MIN_MINUTES <= idle_minutes <= IDLE_MAX_MINUTES:
            raise ValidationError(
                f"Idle lock must be between {IDLE_MIN_MINUTES} and {IDLE_MAX_MINUTES} minutes."
            )
        self.set_meta(META_PROVIDER, provider)
        self.set_meta(META_MODEL, model_name)
        self.set_meta(META_API_KEY, secret)
        self.set_meta(META_IDLE, str(idle_minutes))

    def idle_minutes(self) -> int:
        raw = self.get_meta(META_IDLE, "5") or "5"
        try:
            minutes = int(raw)
        except ValueError:
            return 5
        if not IDLE_MIN_MINUTES <= minutes <= IDLE_MAX_MINUTES:
            return 5
        return minutes

    def list_accounts(self) -> list[AccountSummary]:
        rows = self.connection.execute(
            "SELECT id, name, updated_at FROM accounts ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [
            AccountSummary(
                account_id=int(row["id"]),
                account_name=str(row["name"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def search(self, query: str) -> list[SearchRow]:
        text = query.strip()
        if text:
            pattern = like_pattern(text)
            rows = self.connection.execute(
                """
                SELECT a.id AS account_id, a.name AS account_name,
                       q.id AS question_id, q.question_text, q.answer_text,
                       COALESCE(q.updated_at, a.updated_at) AS updated_at
                FROM accounts a
                LEFT JOIN questions q ON q.account_id = a.id
                WHERE a.name LIKE ? ESCAPE '\\'
                   OR IFNULL(q.question_text, '') LIKE ? ESCAPE '\\'
                   OR IFNULL(q.answer_text, '') LIKE ? ESCAPE '\\'
                ORDER BY a.name COLLATE NOCASE, q.id
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT a.id AS account_id, a.name AS account_name,
                       q.id AS question_id, q.question_text, q.answer_text,
                       COALESCE(q.updated_at, a.updated_at) AS updated_at
                FROM accounts a
                LEFT JOIN questions q ON q.account_id = a.id
                ORDER BY a.name COLLATE NOCASE, q.id
                """
            ).fetchall()
        return [
            SearchRow(
                account_id=int(row["account_id"]),
                account_name=str(row["account_name"]),
                question_id=None if row["question_id"] is None else int(row["question_id"]),
                question_text="" if row["question_text"] is None else str(row["question_text"]),
                answer_text="" if row["answer_text"] is None else str(row["answer_text"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def get_account(self, account_id: int) -> tuple[str, list[QuestionDraft]] | None:
        row = self.connection.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        questions = self.connection.execute(
            """
            SELECT id, question_text, answer_text, canonical_key, answer_kind
            FROM questions
            WHERE account_id = ?
            ORDER BY id
            """,
            (account_id,),
        ).fetchall()
        drafts = [
            QuestionDraft(
                question_text=str(item["question_text"]),
                answer_text=str(item["answer_text"]),
                canonical_key=str(item["canonical_key"]),
                answer_kind=str(item["answer_kind"]),
                question_id=int(item["id"]),
            )
            for item in questions
        ]
        return str(row["name"]), drafts

    def save_account(self, account_id: int | None, name: str, questions: list[QuestionDraft]) -> int:
        account_name = name.strip()
        if not account_name:
            raise ValidationError("Enter an account name.")
        if len(account_name) > NAME_MAX:
            raise ValidationError("The account name is too long.")
        cleaned = [self._clean_question(question) for question in questions]
        self._check_duplicate_questions(cleaned)
        self._check_name(account_id, account_name)
        self._check_answer_collisions(account_id, cleaned)
        now = self._now()
        try:
            with self.connection:
                if account_id is None:
                    cursor = self.connection.execute(
                        "INSERT INTO accounts (name, updated_at) VALUES (?, ?)",
                        (account_name, now),
                    )
                    saved_id = int(cursor.lastrowid)
                else:
                    existing = self.connection.execute(
                        "SELECT id FROM accounts WHERE id = ?",
                        (account_id,),
                    ).fetchone()
                    if existing is None:
                        raise ValidationError("That account no longer exists.")
                    self.connection.execute(
                        "UPDATE accounts SET name = ?, updated_at = ? WHERE id = ?",
                        (account_name, now, account_id),
                    )
                    saved_id = account_id
                self._replace_questions(saved_id, cleaned, now)
        except sqlite3.IntegrityError:
            raise ValidationError("That account or question is already saved.") from None
        return saved_id

    def delete_account(self, account_id: int) -> None:
        with self.connection:
            cursor = self.connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            if cursor.rowcount != 1:
                raise ValidationError("That account no longer exists.")

    def normalized_answers(self, canonical_key: str, exclude_question_ids: set[int]) -> set[str]:
        rows = self.connection.execute(
            "SELECT id, answer_text FROM questions WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchall()
        found: set[str] = set()
        for row in rows:
            if int(row["id"]) in exclude_question_ids:
                continue
            normalized = normalize_answer(str(row["answer_text"]))
            if normalized:
                found.add(normalized)
        return found

    def kind_for_key(self, canonical_key: str) -> str | None:
        row = self.connection.execute(
            "SELECT answer_kind FROM questions WHERE canonical_key = ? LIMIT 1",
            (canonical_key,),
        ).fetchone()
        if row is not None:
            return str(row["answer_kind"])
        row = self.connection.execute(
            "SELECT answer_kind FROM classifications WHERE canonical_key = ? LIMIT 1",
            (canonical_key,),
        ).fetchone()
        if row is None:
            return None
        return str(row["answer_kind"])

    def list_groups(self) -> list[QuestionGroup]:
        kinds: dict[str, str] = {}
        for row in self.connection.execute("SELECT canonical_key, answer_kind FROM questions ORDER BY id"):
            kinds.setdefault(str(row["canonical_key"]), str(row["answer_kind"]))
        for row in self.connection.execute(
            "SELECT canonical_key, answer_kind FROM classifications ORDER BY normalized_question"
        ):
            kinds.setdefault(str(row["canonical_key"]), str(row["answer_kind"]))
        groups: list[QuestionGroup] = []
        for key in sorted(kinds):
            examples = self.connection.execute(
                """
                SELECT question_text FROM questions
                WHERE canonical_key = ?
                ORDER BY id
                LIMIT 3
                """,
                (key,),
            ).fetchall()
            if not examples:
                examples = self.connection.execute(
                    """
                    SELECT normalized_question AS question_text
                    FROM classifications
                    WHERE canonical_key = ?
                    LIMIT 3
                    """,
                    (key,),
                ).fetchall()
            groups.append(
                QuestionGroup(
                    canonical_key=key,
                    answer_kind=kinds[key],
                    examples=tuple(str(item["question_text"]) for item in examples),
                )
            )
        return groups

    def get_classification(self, normalized: str) -> tuple[str, str, str] | None:
        row = self.connection.execute(
            """
            SELECT canonical_key, answer_kind, source
            FROM classifications
            WHERE normalized_question = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            return None
        return str(row["canonical_key"]), str(row["answer_kind"]), str(row["source"])

    def list_classification_questions(self) -> list[tuple[str, str, str]]:
        rows = self.connection.execute(
            "SELECT normalized_question, canonical_key, answer_kind FROM classifications"
        ).fetchall()
        return [
            (str(row["normalized_question"]), str(row["canonical_key"]), str(row["answer_kind"]))
            for row in rows
        ]

    def save_classification(
        self,
        normalized: str,
        canonical_key: str,
        answer_kind: str,
        source: str,
    ) -> None:
        if not normalized or len(normalized) > TEXT_MAX:
            raise ValidationError("That question cannot be grouped.")
        if not is_canonical_key(canonical_key) or answer_kind not in ANSWER_KINDS:
            raise ValidationError("That question group is not valid.")
        if source not in CLASSIFICATION_SOURCES:
            raise ValidationError("That question group is not valid.")
        existing = self.kind_for_key(canonical_key)
        if existing is not None and existing != answer_kind:
            raise ValidationError(
                f"The group '{canonical_key}' already uses the '{existing}' answer type."
            )
        self.connection.execute(
            """
            INSERT INTO classifications (normalized_question, canonical_key, answer_kind, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(normalized_question) DO UPDATE SET
                canonical_key = excluded.canonical_key,
                answer_kind = excluded.answer_kind,
                source = excluded.source
            """,
            (normalized, canonical_key, answer_kind, source),
        )

    def _insert_defaults(self) -> None:
        defaults = (
            (META_SCHEMA, SCHEMA_VERSION),
            (META_PROVIDER, "openrouter"),
            (META_MODEL, DEFAULT_MODELS["openrouter"]),
            (META_API_KEY, ""),
            (META_IDLE, "5"),
        )
        self.connection.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", defaults)

    def _require_schema(self) -> None:
        rows = self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        names = {str(row["name"]) for row in rows}
        if not REQUIRED_TABLES <= names:
            raise VaultError("The vault data could not be read.")
        if self.get_meta(META_SCHEMA) != SCHEMA_VERSION:
            raise VaultError("This vault was created by a different version of the app.")

    def _clean_question(self, draft: QuestionDraft) -> QuestionDraft:
        question = draft.question_text.strip()
        answer = draft.answer_text.strip()
        key = draft.canonical_key.strip()
        kind = draft.answer_kind.strip()
        if not question:
            raise ValidationError("Enter the security question.")
        if not answer:
            raise ValidationError("Enter an answer or generate one.")
        if len(question) > TEXT_MAX or len(answer) > TEXT_MAX:
            raise ValidationError("A question or answer is too long.")
        if not is_canonical_key(key):
            raise ValidationError("Choose a question group before saving.")
        if kind not in ANSWER_KINDS:
            raise ValidationError("Choose a valid answer type.")
        if not normalize_answer(answer):
            raise ValidationError("Enter an answer that contains letters or numbers.")
        self._check_kind_conflict(key, kind, draft.question_id)
        return QuestionDraft(question, answer, key, kind, draft.question_id)

    def _check_kind_conflict(self, key: str, kind: str, question_id: int | None) -> None:
        rows = self.connection.execute(
            "SELECT id, answer_kind FROM questions WHERE canonical_key = ?",
            (key,),
        ).fetchall()
        for row in rows:
            if question_id is not None and int(row["id"]) == question_id:
                continue
            if str(row["answer_kind"]) != kind:
                raise ValidationError(
                    f"The group '{key}' already uses the '{row['answer_kind']}' answer type."
                )

    def _check_duplicate_questions(self, questions: list[QuestionDraft]) -> None:
        seen: set[str] = set()
        for question in questions:
            key = question.question_text.casefold()
            if key in seen:
                raise ValidationError(f"The question '{question.question_text}' is listed more than once.")
            seen.add(key)

    def _check_name(self, account_id: int | None, name: str) -> None:
        row = self.connection.execute(
            "SELECT id FROM accounts WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row is not None and int(row["id"]) != account_id:
            raise ValidationError(f"An account named '{name}' already exists.")

    def _check_answer_collisions(self, account_id: int | None, questions: list[QuestionDraft]) -> None:
        exclude = self._account_question_ids(account_id)
        seen: dict[str, set[str]] = {}
        for question in questions:
            normalized = normalize_answer(question.answer_text)
            bucket = seen.setdefault(
                question.canonical_key,
                set(self.normalized_answers(question.canonical_key, exclude)),
            )
            if normalized in bucket:
                raise CollisionError(question.answer_text, question.canonical_key)
            bucket.add(normalized)

    def _account_question_ids(self, account_id: int | None) -> set[int]:
        if account_id is None:
            return set()
        rows = self.connection.execute(
            "SELECT id FROM questions WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        return {int(row["id"]) for row in rows}

    def _replace_questions(self, account_id: int, questions: list[QuestionDraft], now: str) -> None:
        keep = {question.question_id for question in questions if question.question_id is not None}
        current = self.connection.execute(
            "SELECT id FROM questions WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        for row in current:
            if int(row["id"]) not in keep:
                self.connection.execute(
                    "DELETE FROM questions WHERE id = ? AND account_id = ?",
                    (int(row["id"]), account_id),
                )
        for question in questions:
            if question.question_id is None:
                self.connection.execute(
                    """
                    INSERT INTO questions (
                        account_id, question_text, answer_text, canonical_key, answer_kind, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        question.question_text,
                        question.answer_text,
                        question.canonical_key,
                        question.answer_kind,
                        now,
                    ),
                )
            else:
                updated = self.connection.execute(
                    """
                    UPDATE questions
                    SET question_text = ?, answer_text = ?, canonical_key = ?, answer_kind = ?, updated_at = ?
                    WHERE id = ? AND account_id = ?
                    """,
                    (
                        question.question_text,
                        question.answer_text,
                        question.canonical_key,
                        question.answer_kind,
                        now,
                        question.question_id,
                        account_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise ValidationError("A question on this account could not be updated.")
            self.save_classification(
                normalize_question(question.question_text),
                question.canonical_key,
                question.answer_kind,
                "saved",
            )
