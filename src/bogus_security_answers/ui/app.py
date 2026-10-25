"""Main window: unlock the vault, then search and edit accounts."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import TclError, filedialog, messagebox, ttk

import customtkinter as ctk

from bogus_security_answers.csv_io import export_database, verify_csv
from bogus_security_answers.errors import AppError
from bogus_security_answers.paths import vault_path
from bogus_security_answers.session import Session, derive_new_key
from bogus_security_answers.single_instance import SingleInstance
from bogus_security_answers.ui.account_dialog import AccountDialog
from bogus_security_answers.ui.settings_dialog import SettingsDialog
from bogus_security_answers.ui.verify_dialog import VerifyDialog
from bogus_security_answers.vault import KdfParams, Vault

ERROR_COLOR = ("#b91c1c", "#fca5a5")
MUTED_COLOR = ("#334155", "#cbd5e1")
WARN_COLOR = ("#9a3412", "#fdba74")


class SecurityAnswersApp(ctk.CTk):
    def __init__(
        self,
        vault_file: Path | None = None,
        kdf: KdfParams | None = None,
    ) -> None:
        super().__init__()
        self.title("Security Answers")
        self.geometry("1040x680")
        self.minsize(860, 560)
        self._vault_path = Path(vault_file) if vault_file is not None else vault_path()
        self._kdf = kdf or KdfParams()
        self._session: Session | None = None
        self._idle_after: str | None = None
        self._busy = False
        self._main_queue: queue.Queue = queue.Queue()
        self._row_accounts: dict[str, int] = {}
        self._password_entry: ctk.CTkEntry | None = None
        self._confirm_entry: ctk.CTkEntry | None = None
        self._unlock_button: ctk.CTkButton | None = None
        self._unlock_status: ctk.CTkLabel | None = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Key>", self._note_activity, add="+")
        self.bind_all("<Button>", self._note_activity, add="+")
        self.after(50, self._drain_main_queue)
        self._show_unlock()

    def call_on_main(self, callback) -> None:
        self._main_queue.put(callback)

    def _drain_main_queue(self) -> None:
        try:
            while True:
                callback = self._main_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                try:
                    self.after(50, self._drain_main_queue)
                except TclError:
                    pass

    def note_activity(self) -> None:
        self._arm_idle()

    def bind_activity(self, widget) -> None:
        widget.bind("<Key>", lambda _event: self.note_activity(), add="+")
        widget.bind("<Button>", lambda _event: self.note_activity(), add="+")

    def _note_activity(self, _event=None) -> None:
        if self._session is not None:
            self._arm_idle()

    def _arm_idle(self) -> None:
        self._cancel_idle()
        if self._session is None:
            return
        minutes = self._session.db.idle_minutes()
        self._idle_after = self.after(minutes * 60 * 1000, self.lock)

    def _cancel_idle(self) -> None:
        if self._idle_after is not None:
            try:
                self.after_cancel(self._idle_after)
            except TclError:
                pass
            self._idle_after = None

    def _clear(self) -> None:
        self._cancel_idle()
        for child in list(self.winfo_children()):
            child.destroy()

    def _show_unlock(self) -> None:
        self._clear()
        self.title("Security Answers")
        creating = not self._vault_path.exists()
        frame = ctk.CTkFrame(self)
        frame.pack(expand=True)
        ctk.CTkLabel(frame, text="Security Answers", font=ctk.CTkFont(size=28, weight="bold")).pack(
            pady=(28, 8)
        )
        if creating:
            message = (
                "Create a master password. There is no password reset. "
                "If you forget this password, the vault cannot be opened."
            )
            color = WARN_COLOR
            button_text = "Create vault"
        else:
            message = "Enter your master password to unlock the vault."
            color = MUTED_COLOR
            button_text = "Unlock"
        ctk.CTkLabel(frame, text=message, wraplength=520, text_color=color, justify="left").pack(
            padx=24, pady=(0, 12)
        )
        self._password_entry = ctk.CTkEntry(frame, show="*", width=360, placeholder_text="Master password")
        self._password_entry.pack(pady=6)
        self._confirm_entry = None
        if creating:
            self._confirm_entry = ctk.CTkEntry(
                frame, show="*", width=360, placeholder_text="Confirm master password"
            )
            self._confirm_entry.pack(pady=6)
        show_password = ctk.BooleanVar(value=False)

        def toggle() -> None:
            secret = "" if show_password.get() else "*"
            assert self._password_entry is not None
            self._password_entry.configure(show=secret)
            if self._confirm_entry is not None:
                self._confirm_entry.configure(show=secret)

        ctk.CTkCheckBox(frame, text="Show password", variable=show_password, command=toggle).pack(pady=6)
        self._unlock_status = ctk.CTkLabel(frame, text="", wraplength=520, text_color=ERROR_COLOR)
        self._unlock_status.pack(pady=6)
        self._unlock_button = ctk.CTkButton(frame, text=button_text, command=self._submit_unlock)
        self._unlock_button.pack(pady=8)
        ctk.CTkButton(frame, text="Quit", fg_color="transparent", command=self._on_close).pack(pady=(0, 8))
        ctk.CTkLabel(
            frame,
            text=f"Vault: {self._vault_path}",
            wraplength=520,
            text_color=MUTED_COLOR,
        ).pack(padx=24, pady=(8, 20))
        self._password_entry.bind("<Return>", lambda _event: self._submit_unlock())
        if self._confirm_entry is not None:
            self._confirm_entry.bind("<Return>", lambda _event: self._submit_unlock())
        self._password_entry.focus_set()

    def _submit_unlock(self) -> None:
        if self._busy or self._password_entry is None or self._unlock_button is None:
            return
        password = self._password_entry.get()
        confirm = self._confirm_entry.get() if self._confirm_entry is not None else None
        creating = confirm is not None
        self._password_entry.delete(0, "end")
        if self._confirm_entry is not None:
            self._confirm_entry.delete(0, "end")
        if creating and password != confirm:
            self._set_unlock_status("The passwords do not match.")
            return
        self._busy = True
        self._unlock_button.configure(state="disabled")
        self._set_unlock_status("Unlocking...", error=False)
        path = self._vault_path
        kdf = self._kdf

        def work() -> None:
            try:
                if creating:
                    key, params = derive_new_key(password, kdf)
                    self.call_on_main(lambda opened_key=key, opened_params=params: self._create_on_main(opened_key, opened_params))
                else:
                    key, params, payload = Vault(path).unlock(password)
                    self.call_on_main(
                        lambda opened_key=key, opened_params=params, opened_payload=payload: self._open_on_main(
                            opened_key, opened_params, opened_payload
                        )
                    )
            except AppError as exc:
                message = str(exc)
                self.call_on_main(lambda text=message: self._unlock_failed(text))
            except Exception:
                self.call_on_main(lambda: self._unlock_failed("The vault could not be opened."))

        threading.Thread(target=work, daemon=True).start()

    def _create_on_main(self, key: bytes, params: KdfParams) -> None:
        if not self.winfo_exists():
            return
        try:
            session = Session.from_new_key(self._vault_path, key, params)
        except AppError as exc:
            self._unlock_failed(str(exc))
            return
        self._unlocked(session)

    def _open_on_main(self, key: bytes, params: KdfParams, payload: bytes) -> None:
        if not self.winfo_exists():
            return
        try:
            session = Session.from_payload(self._vault_path, key, params, payload)
        except AppError as exc:
            self._unlock_failed(str(exc))
            return
        self._unlocked(session)

    def _unlock_failed(self, message: str) -> None:
        self._busy = False
        if not self.winfo_exists():
            return
        if self._unlock_button is not None and self._unlock_button.winfo_exists():
            self._unlock_button.configure(state="normal")
        self._set_unlock_status(message)

    def _set_unlock_status(self, message: str, *, error: bool = True) -> None:
        if self._unlock_status is not None and self._unlock_status.winfo_exists():
            self._unlock_status.configure(text=message, text_color=ERROR_COLOR if error else MUTED_COLOR)

    def _unlocked(self, session: Session) -> None:
        self._busy = False
        if not self.winfo_exists():
            session.lock()
            return
        self._session = session
        self._show_main()

    def lock(self) -> None:
        session = self._session
        self._session = None
        self._cancel_idle()
        for child in list(self.winfo_children()):
            child.destroy()
        if session is not None:
            session.lock()
        self._show_unlock()

    def _show_main(self) -> None:
        self._clear()
        self.title("Security Answers")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(side="top", fill="x", padx=16, pady=(16, 8))
        self._search = ctk.CTkEntry(top, placeholder_text="Search accounts, questions, and answers")
        self._search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._search.bind("<Return>", lambda _event: self.refresh())
        ctk.CTkButton(top, text="Search", width=100, command=self.refresh).pack(side="left")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=16, pady=(8, 16))
        ctk.CTkButton(buttons, text="Add Account", command=self._add_account).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Edit Account", command=self._edit_account).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Delete Account", command=self._delete_account).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Export CSV", command=self._export).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Verify CSV", command=self._verify).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Settings", command=self._settings).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Lock", fg_color="transparent", command=self.lock).pack(side="right")

        self._main_status = ctk.CTkLabel(self, text="", anchor="w", text_color=MUTED_COLOR)
        self._main_status.pack(side="bottom", fill="x", padx=16)

        tree_frame = ctk.CTkFrame(self)
        tree_frame.pack(side="top", fill="both", expand=True, padx=16, pady=8)
        self._style_tree()
        columns = ("account", "question", "answer", "updated")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Security.Treeview",
        )
        self._tree.heading("account", text="Account")
        self._tree.heading("question", text="Question")
        self._tree.heading("answer", text="Answer")
        self._tree.heading("updated", text="Last Edited")
        self._tree.column("account", width=180, stretch=True, anchor="w")
        self._tree.column("question", width=280, stretch=True, anchor="w")
        self._tree.column("answer", width=220, stretch=True, anchor="w")
        self._tree.column("updated", width=190, stretch=False, anchor="w")
        self._apply_account_columns()
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._tree.bind("<Double-1>", lambda _event: self._edit_account())
        self.refresh()
        self._arm_idle()
        self._search.focus_set()

    def _style_tree(self) -> None:
        mode = ctk.get_appearance_mode()
        style = ttk.Style(self)
        style.theme_use("clam")
        if mode == "Dark":
            background, foreground, heading = "#1f2933", "#e5e7eb", "#111827"
            selected = "#1d4ed8"
        else:
            background, foreground, heading = "#ffffff", "#111827", "#e5e7eb"
            selected = "#2563eb"
        style.configure(
            "Security.Treeview",
            background=background,
            foreground=foreground,
            fieldbackground=background,
            rowheight=28,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Security.Treeview.Heading",
            background=heading,
            foreground=foreground,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Security.Treeview",
            background=[("selected", selected)],
            foreground=[("selected", "#ffffff")],
        )

    def _apply_account_columns(self) -> None:
        self._tree.configure(displaycolumns=("account", "updated"))
        self._tree.column("account", width=520, stretch=True)
        self._tree.column("updated", width=220, stretch=False)

    def _apply_search_columns(self) -> None:
        self._tree.configure(displaycolumns=("account", "question", "answer", "updated"))
        self._tree.column("account", width=180, stretch=False)
        self._tree.column("question", width=280, stretch=True)
        self._tree.column("answer", width=220, stretch=True)
        self._tree.column("updated", width=190, stretch=False)

    def refresh(self) -> None:
        if self._session is None:
            return
        query = self._search.get().strip() if hasattr(self, "_search") else ""
        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)
        self._row_accounts.clear()
        if query:
            self._apply_search_columns()
            rows = self._session.db.search(query)
            for index, row in enumerate(rows):
                iid = f"{row.account_id}:{row.question_id or 0}:{index}"
                self._tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(row.account_name, row.question_text, row.answer_text, row.updated_at),
                )
                self._row_accounts[iid] = row.account_id
            if rows:
                label = "match" if len(rows) == 1 else "matches"
                self._main_status.configure(text=f"{len(rows)} {label}")
            else:
                self._main_status.configure(text="No matches.")
        else:
            self._apply_account_columns()
            accounts = self._session.db.list_accounts()
            for account in accounts:
                iid = str(account.account_id)
                self._tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(account.account_name, "", "", account.updated_at),
                )
                self._row_accounts[iid] = account.account_id
            if accounts:
                label = "account" if len(accounts) == 1 else "accounts"
                self._main_status.configure(text=f"{len(accounts)} {label}")
            else:
                self._main_status.configure(text="No accounts yet. Click Add Account.")
        self.note_activity()

    def _selected_account_id(self) -> int | None:
        selected = self._tree.selection()
        if not selected:
            return None
        return self._row_accounts.get(selected[0])

    def _add_account(self) -> None:
        if self._session is None:
            return
        self.note_activity()
        AccountDialog(self, self._session, None, on_saved=self.refresh)

    def _edit_account(self) -> None:
        if self._session is None:
            return
        account_id = self._selected_account_id()
        if account_id is None:
            messagebox.showinfo("Edit Account", "Select an account first.", parent=self)
            return
        self.note_activity()
        AccountDialog(self, self._session, account_id, on_saved=self.refresh)

    def _delete_account(self) -> None:
        if self._session is None:
            return
        account_id = self._selected_account_id()
        if account_id is None:
            messagebox.showinfo("Delete Account", "Select an account first.", parent=self)
            return
        loaded = self._session.db.get_account(account_id)
        if loaded is None:
            self.refresh()
            return
        name = loaded[0]
        if not messagebox.askyesno(
            "Delete Account",
            f"Delete account '{name}' and all of its questions?",
            parent=self,
            default=messagebox.NO,
        ):
            return
        try:
            self._session.db.delete_account(account_id)
            self._session.persist()
        except AppError as exc:
            messagebox.showerror("Delete Account", str(exc), parent=self)
            return
        self.refresh()

    def _export(self) -> None:
        if self._session is None:
            return
        self.note_activity()
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Export CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="security-answers.csv",
        )
        if not selected:
            return
        try:
            count = export_database(self._session.db, Path(selected), self._session.vault.path)
        except AppError as exc:
            messagebox.showerror("Export CSV", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Export CSV",
            f"Exported {count} row(s).\n\nThe CSV file is not encrypted. Anyone who can open it can read the answers.",
            parent=self,
        )

    def _verify(self) -> None:
        if self._session is None:
            return
        self.note_activity()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Verify CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            report = verify_csv(self._session.db, Path(selected))
        except AppError as exc:
            messagebox.showerror("Verify CSV", str(exc), parent=self)
            return
        VerifyDialog(self, report)

    def _settings(self) -> None:
        if self._session is None:
            return
        self.note_activity()
        SettingsDialog(self, self._session)

    def _on_close(self) -> None:
        self._cancel_idle()
        session = self._session
        self._session = None
        if session is not None:
            session.lock()
        self.destroy()


def _tell_already_running() -> None:
    root = ctk.CTk()
    root.withdraw()
    messagebox.showinfo("Security Answers", "Security Answers is already running.", parent=root)
    root.destroy()


def main() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    instance = SingleInstance()
    if not instance.acquire():
        _tell_already_running()
        return
    app = SecurityAnswersApp()
    try:
        app.mainloop()
    finally:
        instance.release()
