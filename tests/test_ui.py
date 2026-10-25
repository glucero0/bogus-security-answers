import time

from bogus_security_answers.ui.app import SecurityAnswersApp
from bogus_security_answers.vault import KdfParams

PASSWORD = "correct horse battery"
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


def test_unlock_add_account_and_generate(tmp_path):
    vault = tmp_path / "vault.db"
    app = SecurityAnswersApp(vault_file=vault, kdf=FAST)
    try:
        app.update()
        assert app._password_entry is not None
        assert app._confirm_entry is not None
        app._password_entry.insert(0, PASSWORD)
        app._confirm_entry.insert(0, PASSWORD)
        app._submit_unlock()
        for _ in range(100):
            app.update()
            if app._session is not None:
                break
            time.sleep(0.05)
        assert app._session is not None
        assert vault.is_file()
        assert b"correct horse" not in vault.read_bytes()

        from bogus_security_answers.ui.account_dialog import AccountDialog

        dialog = AccountDialog(app, app._session, None, on_saved=app.refresh)
        dialog.update()
        dialog._name.insert(0, "Gmail")
        row = dialog._rows[0]
        row.question.insert(0, "What city were you born in?")
        dialog._set_group(row, "birth_city", "us_city")
        dialog._generate(row)
        dialog.update()
        answer = row.answer.get()
        assert answer
        assert "birth_city" in row.group.get()
        dialog._save()
        app.update()

        values = [app._tree.item(item, "values") for item in app._tree.get_children()]
        assert len(values) == 1
        assert values[0][0] == "Gmail"
        assert values[0][1] == ""
        assert values[0][2] == ""
        assert app._tree["displaycolumns"] == ("account", "updated")
        stored = vault.read_bytes()
        assert b"Gmail" not in stored
        assert answer.encode("utf-8") not in stored

        app._search.insert(0, answer)
        app.refresh()
        matches = [app._tree.item(item, "values") for item in app._tree.get_children()]
        assert len(matches) == 1
        assert matches[0][0] == "Gmail"
        assert matches[0][1] == "What city were you born in?"
        assert matches[0][2] == answer
        assert app._tree["displaycolumns"] == ("account", "question", "answer", "updated")

        app._search.delete(0, "end")
        app.refresh()
        listed = [app._tree.item(item, "values")[0] for item in app._tree.get_children()]
        assert listed == ["Gmail"]

        from bogus_security_answers.ui.settings_dialog import SettingsDialog

        settings = SettingsDialog(app, app._session)
        settings.update()
        settings._save()
        settings.update()
        assert settings.status.cget("text") == "Settings saved."
        assert settings.status.winfo_y() < settings.current.winfo_y()
        assert settings.vault_path.cget("text") == str(vault)
        assert app._session.db.get_meta("ai_provider") == "openrouter"
        settings.destroy()

        app.lock()
        app.update()
        assert app._session is None
        assert app._password_entry is not None
    finally:
        if app.winfo_exists():
            app._on_close()
