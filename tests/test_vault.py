import pytest

from bogus_security_answers.errors import PasswordError, UnlockError
from bogus_security_answers.models import QuestionDraft
from bogus_security_answers.session import Session
from bogus_security_answers.vault import MAGIC, KdfParams, Vault, derive_key

PASSWORD = "correct horse battery"
NEW_PASSWORD = "battery horse correct"
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


def test_roundtrip_hides_plaintext(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.db.save_account(
        None,
        "Gmail",
        [QuestionDraft("City of birth", "Boise, ID", "birth_city", "us_city")],
    )
    session.db.update_settings("openrouter", "google/gemini-2.5-flash", "sk-test-key-value", 5)
    session.persist()
    session.lock()

    stored = path.read_bytes()
    assert stored.startswith(MAGIC)
    assert b"Boise" not in stored
    assert b"Gmail" not in stored
    assert b"sk-test-key-value" not in stored
    assert not path.with_name(path.name + ".tmp").exists()

    opened = Session.unlock(path, PASSWORD)
    rows = opened.db.search("")
    assert rows[0].account_name == "Gmail"
    assert rows[0].answer_text == "Boise, ID"
    assert opened.db.get_meta("ai_api_key") == "sk-test-key-value"
    opened.lock()


def test_wrong_password_leaves_the_file_unchanged(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.lock()
    before = path.read_bytes()
    with pytest.raises(UnlockError):
        Session.unlock(path, "wrong password!!")
    assert path.read_bytes() == before


def test_short_password_is_rejected(tmp_path):
    with pytest.raises(PasswordError):
        Session.create(tmp_path / "vault.db", "too-short", FAST)


def test_tampered_ciphertext_does_not_open(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.lock()
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(data)
    with pytest.raises(UnlockError):
        Session.unlock(path, PASSWORD)


def test_tampered_header_does_not_open(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.lock()
    data = bytearray(path.read_bytes())
    data[len(MAGIC) + 6] ^= 0x01
    path.write_bytes(data)
    with pytest.raises(UnlockError):
        Session.unlock(path, PASSWORD)


def test_backup_is_the_previous_snapshot(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.db.save_account(None, "First", [])
    session.persist()
    session.db.save_account(None, "Second", [])
    session.persist()
    session.lock()

    backup = Session.unlock(path.with_name(path.name + ".bak"), PASSWORD)
    assert [row.account_name for row in backup.db.search("")] == ["First"]
    backup.lock()
    current = Session.unlock(path, PASSWORD)
    assert [row.account_name for row in current.db.search("")] == ["First", "Second"]
    current.lock()


def test_password_change_rewrites_the_backup(tmp_path):
    path = tmp_path / "vault.db"
    session = Session.create(path, PASSWORD, FAST)
    session.db.save_account(None, "Gmail", [])
    session.persist()
    session.change_password(PASSWORD, NEW_PASSWORD)
    session.lock()

    opened = Session.unlock(path, NEW_PASSWORD)
    assert opened.db.search("")[0].account_name == "Gmail"
    opened.lock()
    backup = Session.unlock(path.with_name(path.name + ".bak"), NEW_PASSWORD)
    assert backup.db.search("")[0].account_name == "Gmail"
    backup.lock()
    with pytest.raises(UnlockError):
        Session.unlock(path, PASSWORD)
    with pytest.raises(UnlockError):
        Session.unlock(path.with_name(path.name + ".bak"), PASSWORD)


def test_same_password_is_not_accepted_as_a_change(tmp_path):
    session = Session.create(tmp_path / "vault.db", PASSWORD, FAST)
    with pytest.raises(PasswordError):
        session.change_password(PASSWORD, PASSWORD)


def test_direct_seal_roundtrip(tmp_path):
    path = tmp_path / "vault.db"
    vault = Vault(path)
    params = KdfParams(time_cost=1, memory_cost=8, parallelism=1, salt=b"0123456789abcdef")
    key = derive_key(PASSWORD, params)
    vault.write_encrypted(key, params, b"payload-bytes", refresh_backup=False)
    unlocked_key, unlocked_params, payload = vault.unlock(PASSWORD)
    assert payload == b"payload-bytes"
    assert unlocked_key == key
    assert unlocked_params.salt == params.salt
