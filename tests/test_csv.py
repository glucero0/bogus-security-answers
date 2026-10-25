import pytest

from bogus_security_answers.csv_io import export_database, verify_csv
from bogus_security_answers.db import META_API_KEY, Database
from bogus_security_answers.errors import ValidationError
from bogus_security_answers.models import QuestionDraft


def populated_database() -> Database:
    database = Database.create_empty(now=lambda: "2026-03-01T12:00:00+00:00")
    database.set_meta(META_API_KEY, "sk-export-secret")
    database.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "=Boise, ID", "birth_city", "us_city")],
    )
    database.save_account(None, "Empty", [])
    return database


def test_export_escapes_formulas_and_omits_the_api_key(tmp_path):
    database = populated_database()
    destination = tmp_path / "answers.csv"
    vault = tmp_path / "vault.db"
    vault.write_bytes(b"placeholder")
    count = export_database(database, destination, vault)
    text = destination.read_text(encoding="utf-8-sig")
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    assert count == 2
    assert "Account Name,Question,Answer,Last Edited" in text
    assert "'=Boise, ID" in text
    assert "sk-export-secret" not in text
    assert "Empty" in text


def test_verify_matches_after_formula_prefix_is_removed(tmp_path):
    database = populated_database()
    destination = tmp_path / "answers.csv"
    export_database(database, destination, tmp_path / "vault.db")
    report = verify_csv(database, destination)
    assert report.matched == 2
    assert report.different == []
    assert report.only_csv == []
    assert report.only_vault == []
    assert "The CSV matches the vault." in report.summary()


def test_verify_reports_differences_and_extra_rows(tmp_path):
    database = populated_database()
    destination = tmp_path / "answers.csv"
    export_database(database, destination, tmp_path / "vault.db")
    text = destination.read_text(encoding="utf-8-sig")
    text = text.replace("'=Boise, ID", "Reno, NV")
    text += "Bank,First pet,Milo,2026-03-01T12:00:00+00:00\n"
    destination.write_text(text, encoding="utf-8-sig")
    report = verify_csv(database, destination)
    assert report.matched == 1
    assert len(report.different) == 1
    assert report.different[0].csv_answer == "Reno, NV"
    assert report.different[0].vault_answer == "=Boise, ID"
    assert report.only_csv == [("Bank", "First pet")]
    assert report.only_vault == []


def test_verify_reports_rows_missing_from_the_csv(tmp_path):
    database = populated_database()
    destination = tmp_path / "answers.csv"
    destination.write_text(
        "Account Name,Question,Answer,Last Edited\n"
        'Gmail,City of birth,"\'=Boise, ID",2026-03-01T12:00:00+00:00\n',
        encoding="utf-8-sig",
    )
    report = verify_csv(database, destination)
    assert report.matched == 1
    assert report.only_vault == [("Empty", "")]


def test_bad_header_is_rejected(tmp_path):
    path = tmp_path / "answers.csv"
    path.write_text("Name,Secret\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        verify_csv(Database.create_empty(), path)


def test_export_refuses_the_vault_path(tmp_path):
    vault = tmp_path / "vault.db"
    vault.write_bytes(b"vault")
    with pytest.raises(ValidationError):
        export_database(Database.create_empty(), vault, vault)


def test_legitimate_apostrophe_is_not_stripped(tmp_path):
    database = Database.create_empty(now=lambda: "2026-03-01T12:00:00+00:00")
    database.save_account(
        None,
        "Gmail",
        [QuestionDraft("First pet", "'Milo", "first_pet", "first_name")],
    )
    destination = tmp_path / "answers.csv"
    export_database(database, destination, tmp_path / "vault.db")
    report = verify_csv(database, destination)
    assert report.matched == 1
    assert report.different == []
