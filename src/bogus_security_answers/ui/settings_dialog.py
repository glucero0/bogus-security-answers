"""AI provider settings and master-password change."""

from __future__ import annotations

import customtkinter as ctk

from bogus_security_answers.db import META_API_KEY, META_IDLE, META_MODEL, META_PROVIDER
from bogus_security_answers.errors import AppError
from bogus_security_answers.providers import DEFAULT_MODELS, build_provider
from bogus_security_answers.session import Session
from bogus_security_answers.ui.invoke import invoke_on_main

ERROR_COLOR = ("#b91c1c", "#fca5a5")
OK_COLOR = ("#166534", "#86efac")
MUTED_COLOR = ("#334155", "#cbd5e1")


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, session: Session) -> None:
        super().__init__(master)
        self.session = session
        self.title("Settings")
        self.geometry("520x720")
        self.minsize(460, 560)
        self.transient(master)
        self._busy = False

        provider = session.db.get_meta(META_PROVIDER) or "openrouter"
        model = session.db.get_meta(META_MODEL) or DEFAULT_MODELS.get(provider, "")
        api_key = session.db.get_meta(META_API_KEY) or ""
        idle = session.db.get_meta(META_IDLE) or "5"

        frame = ctk.CTkScrollableFrame(self)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(frame, text="AI provider", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            frame,
            text="Question text is sent only when a question cannot be grouped on this computer. Answers are not sent.",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))
        self.provider = ctk.StringVar(value=provider)
        ctk.CTkRadioButton(
            frame,
            text="OpenRouter",
            variable=self.provider,
            value="openrouter",
            command=self._provider_changed,
        ).pack(anchor="w")
        ctk.CTkRadioButton(
            frame,
            text="Gemini",
            variable=self.provider,
            value="gemini",
            command=self._provider_changed,
        ).pack(anchor="w", pady=(4, 8))

        ctk.CTkLabel(frame, text="Model").pack(anchor="w")
        self.model = ctk.CTkEntry(frame, width=420)
        self.model.insert(0, model)
        self.model.pack(anchor="w", pady=(4, 8))

        ctk.CTkLabel(frame, text="API key").pack(anchor="w")
        self.api_key = ctk.CTkEntry(frame, width=420, show="*")
        if api_key:
            self.api_key.insert(0, api_key)
        self.api_key.pack(anchor="w", pady=(4, 4))
        self._show_key = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(frame, text="Show API key", variable=self._show_key, command=self._toggle_key).pack(
            anchor="w", pady=(0, 8)
        )

        ctk.CTkLabel(frame, text="Idle lock (minutes)").pack(anchor="w")
        self.idle = ctk.CTkEntry(frame, width=120)
        self.idle.insert(0, idle)
        self.idle.pack(anchor="w", pady=(4, 8))

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(actions, text="Save", width=120, command=self._save).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Test connection", width=140, command=self._test).pack(side="left")
        self.status = ctk.CTkLabel(frame, text="", wraplength=440, justify="left", text_color=MUTED_COLOR)
        self.status.pack(anchor="w", pady=(4, 8))

        ctk.CTkLabel(frame, text="Master password", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(16, 4)
        )
        ctk.CTkLabel(frame, text="Current password").pack(anchor="w")
        self.current = ctk.CTkEntry(frame, width=420, show="*")
        self.current.pack(anchor="w", pady=(4, 4))
        ctk.CTkLabel(frame, text="New password").pack(anchor="w")
        self.new = ctk.CTkEntry(frame, width=420, show="*")
        self.new.pack(anchor="w", pady=(4, 4))
        ctk.CTkLabel(frame, text="Confirm new password").pack(anchor="w")
        self.confirm = ctk.CTkEntry(frame, width=420, show="*")
        self.confirm.pack(anchor="w", pady=(4, 8))
        ctk.CTkButton(frame, text="Change password", command=self._change_password).pack(anchor="w")

        ctk.CTkLabel(frame, text="Database", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(16, 4)
        )
        ctk.CTkLabel(
            frame,
            text="The encrypted vault file on this computer.",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        self.vault_path = ctk.CTkLabel(
            frame,
            text=str(session.vault.path),
            wraplength=440,
            justify="left",
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.vault_path.pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(frame, text="Copy path", width=120, command=self._copy_path).pack(anchor="w")

        self.grab_set()
        self.lift()
        self.bind("<Escape>", lambda _event: self.destroy())
        if hasattr(master, "bind_activity"):
            master.bind_activity(self)

    def _provider_changed(self) -> None:
        current = self.model.get().strip()
        defaults = set(DEFAULT_MODELS.values())
        if not current or current in defaults:
            self.model.delete(0, "end")
            self.model.insert(0, DEFAULT_MODELS[self.provider.get()])

    def _toggle_key(self) -> None:
        self.api_key.configure(show="" if self._show_key.get() else "*")

    def _copy_path(self) -> None:
        path = str(self.session.vault.path)
        self.clipboard_clear()
        self.clipboard_append(path)
        self.update_idletasks()
        self._set_status("Database path copied.")

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status.configure(text=message, text_color=ERROR_COLOR if error else OK_COLOR)

    def _save(self) -> None:
        try:
            minutes = int(self.idle.get().strip())
        except ValueError:
            self._set_status("Idle lock must be a whole number of minutes.", error=True)
            return
        try:
            self.session.db.update_settings(
                self.provider.get(),
                self.model.get(),
                self.api_key.get(),
                minutes,
            )
            self.session.persist()
        except AppError as exc:
            self._set_status(str(exc), error=True)
            return
        if hasattr(self.master, "note_activity"):
            self.master.note_activity()
        self._set_status("Settings saved.")

    def _test(self) -> None:
        if self._busy:
            return
        try:
            provider = build_provider(self.provider.get(), self.model.get(), self.api_key.get())
        except AppError as exc:
            self._set_status(str(exc), error=True)
            return
        if provider is None:
            self._set_status("Add an API key first.", error=True)
            return
        self._busy = True
        self._set_status("Checking the API key...")
        self.update_idletasks()

        def work() -> None:
            try:
                provider.test_connection()
                message = "The API key works."
                error = False
            except AppError as exc:
                message = str(exc)
                error = True
            finally:
                provider.close()
            invoke_on_main(self, lambda text=message, failed=error: self._test_done(text, failed))

        import threading

        threading.Thread(target=work, daemon=True).start()

    def _test_done(self, message: str, error: bool) -> None:
        self._busy = False
        if self.winfo_exists():
            self._set_status(message, error=error)

    def _change_password(self) -> None:
        if self._busy:
            return
        new = self.new.get()
        if new != self.confirm.get():
            self._set_status("The new passwords do not match.", error=True)
            return
        self._busy = True
        self._set_status("Updating the master password...")
        self.update_idletasks()
        if hasattr(self.master, "note_activity"):
            self.master.note_activity()
        try:
            self.session.change_password(self.current.get(), new)
        except AppError as exc:
            self._busy = False
            self._set_status(str(exc), error=True)
            return
        self.current.delete(0, "end")
        self.new.delete(0, "end")
        self.confirm.delete(0, "end")
        self._busy = False
        self._set_status("Master password updated.")
