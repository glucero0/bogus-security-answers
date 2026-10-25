import sqlite3

import pytest

from bogus_security_answers.db import Database
from bogus_security_answers.errors import CollisionError, ValidationError
from bogus_security_answers.models import QuestionDraft


class Clock:
    def __init__(self) -> None:
        self.value = "2026-01-01T00:00:00+00:00"

    def __call__(self) -> str:
        return self.value


def test_search_crud_and_cascade():
    clock = Clock()
    database = Database.create_empty(now=clock)
    account_id = database.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city")],
    )
    clock.value = "2026-02-01T00:00:00+00:00"
    database.save_account(
        account_id,
        "Gmail",
        [QuestionDraft("City of birth", "Reno, NV", "birth_city", "us_city", question_id=1)],
    )
    rows = database.search("reno")
    assert rows[0].answer_text == "Reno, NV"
    assert rows[0].updated_at == "2026-02-01T00:00:00+00:00"
    database.delete_account(account_id)
    assert database.search("") == []


def test_account_names_are_case_insensitive():
    database = Database.create_empty()
    database.save_account(None, "Gmail", [])
    with pytest.raises(ValidationError):
        database.save_account(None, "gmail", [])


def test_duplicate_question_on_one_account_is_rejected():
    database = Database.create_empty()
    with pytest.raises(ValidationError):
        database.save_account(
            None,
            "Gmail",
            [
                QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city"),
                QuestionDraft("city of birth", "Reno, NV", "birth_city", "us_city"),
            ],
        )


def test_same_group_cannot_reuse_an_answer_across_accounts():
    database = Database.create_empty()
    database.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city")],
    )
    with pytest.raises(CollisionError):
        database.save_account(
            None,
            "Bank",
            [QuestionDraft("Where were you born?", "Boise ID", "birth_city", "us_city")],
        )


def test_different_groups_may_reuse_text():
    database = Database.create_empty()
    database.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city")],
    )
    database.save_account(
        None,
        "Bank",
        [QuestionDraft("Mother's maiden name", "Boise, ID", "mothers_maiden_name", "last_name")],
    )
    assert len(database.search("")) == 2


def test_search_escapes_like_wildcards():
    database = Database.create_empty()
    database.save_account(None, "100% ready", [])
    database.save_account(None, "Other", [])
    database.save_account(None, "a_b", [])
    database.save_account(None, "axb", [])
    assert [row.account_name for row in database.search("%")] == ["100% ready"]
    assert [row.account_name for row in database.search("a_b")] == ["a_b"]


def test_list_accounts_is_one_row_per_account():
    database = Database.create_empty()
    database.save_account(
        None,
        "PayPal",
        [
            QuestionDraft("First school", "Andrews Middle School", "first_school", "school"),
            QuestionDraft("First pet", "Christopher", "first_pet", "first_name"),
        ],
    )
    database.save_account(None, "Gmail", [])
    accounts = database.list_accounts()
    assert [account.account_name for account in accounts] == ["Gmail", "PayPal"]


def test_empty_search_includes_accounts_without_questions():
    database = Database.create_empty()
    database.save_account(None, "Empty", [])
    rows = database.search("")
    assert rows[0].account_name == "Empty"
    assert rows[0].question_text == ""
    assert rows[0].answer_text == ""


def test_removed_questions_are_deleted():
    database = Database.create_empty()
    account_id = database.save_account(
        None,
        "Gmail",
        [
            QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city"),
            QuestionDraft("First pet", "Milo", "first_pet", "first_name"),
        ],
    )
    _name, questions = database.get_account(account_id)
    kept = questions[0]
    database.save_account(account_id, "Gmail", [kept])
    _name, remaining = database.get_account(account_id)
    assert [question.question_text for question in remaining] == ["City of birth"]


def test_foreign_key_rejects_an_orphan_question():
    database = Database.create_empty()
    with pytest.raises(sqlite3.IntegrityError):
        database.connection.execute(
            """
            INSERT INTO questions (
                account_id, question_text, answer_text, canonical_key, answer_kind, updated_at
            ) VALUES (999, 'City', 'Boise, ID', 'birth_city', 'us_city', '2026-01-01T00:00:00+00:00')
            """
        )


def test_settings_reject_a_bad_model_and_idle_timeout():
    database = Database.create_empty()
    with pytest.raises(ValidationError):
        database.update_settings("openrouter", "bad model", "", 5)
    with pytest.raises(ValidationError):
        database.update_settings("gemini", "google/gemini-2.5-flash", "", 5)
    with pytest.raises(ValidationError):
        database.update_settings("openrouter", "google/gemini-2.5-flash", "key", 0)
