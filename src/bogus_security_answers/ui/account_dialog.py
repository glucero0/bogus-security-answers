"""Add or edit one account and its security questions."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from tkinter import TclError, messagebox

import customtkinter as ctk

from bogus_security_answers.classify import (
    Classifier,
    interpret_model_json,
    keyword_classification,
    provider_for,
)
from bogus_security_answers.errors import AppError
from bogus_security_answers.generate import ANSWER_KINDS, generate_unique_answer
from bogus_security_answers.models import Classification, QuestionDraft
from bogus_security_answers.session import Session
from bogus_security_answers.text import is_canonical_key, normalize_answer, normalize_question, slugify_question
from bogus_security_answers.ui.invoke import invoke_on_main

UNGROUPED = "Not grouped"
ERROR_COLOR = ("#b91c1c", "#fca5a5")
OK_COLOR = ("#166534", "#86efac")
MUTED_COLOR = ("#334155", "#cbd5e1")


def group_label(key: str, kind: str) -> str:
    return f"{key} ({kind})"


def parse_group_label(label: str) -> tuple[str, str] | None:
    if label == UNGROUPED or not label.endswith(")") or " (" not in label:
        return None
    key, kind = label.rsplit(" (", 1)
    kind = kind[:-1]
    if is_canonical_key(key) and kind in ANSWER_KINDS:
        return key, kind
    return None


@dataclass
class QuestionRow:
    frame: ctk.CTkFrame
    question: ctk.CTkEntry
    answer: ctk.CTkEntry
    group: ctk.CTkOptionMenu
    generate: ctk.CTkButton
    question_id: int | None
    busy: bool = False


class AccountDialog(ctk.CTkToplevel):
    def __init__(self, master, session: Session, account_id: int | None, on_saved) -> None:
        super().__init__(master)
        self.session = session
        self.account_id = account_id
        self._on_saved = on_saved
        self._rows: list[QuestionRow] = []
        self.title("Edit Account" if account_id is not None else "Add Account")
        self.geometry("920x660")
        self.minsize(760, 480)
        self.transient(master)

        loaded_name = ""
        loaded_questions: list[QuestionDraft] = []
        if account_id is not None:
            loaded = session.db.get_account(account_id)
            if loaded is None:
                messagebox.showerror("Edit Account", "That account no longer exists.", parent=master)
                self.destroy()
                return
            loaded_name, loaded_questions = loaded

        ctk.CTkLabel(self, text="Account name").pack(anchor="w", padx=16, pady=(16, 0))
        self._name = ctk.CTkEntry(self, placeholder_text="Account name")
        self._name.pack(fill="x", padx=16, pady=(4, 8))
        if loaded_name:
            self._name.insert(0, loaded_name)

        ctk.CTkLabel(
            self,
            text=(
                "Generate sends the question text to OpenRouter or Gemini when it cannot be "
                "grouped on this computer. Answers are not sent."
            ),
            wraplength=860,
            justify="left",
            text_color=MUTED_COLOR,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        self._questions = ctk.CTkScrollableFrame(self)
        self._questions.pack(fill="both", expand=True, padx=16, pady=4)

        self._status = ctk.CTkLabel(self, text="", wraplength=860, justify="left", text_color=MUTED_COLOR)
        self._status.pack(anchor="w", padx=16, pady=(4, 4))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(buttons, text="Add", width=100, command=lambda: self._add_row(None)).pack(side="left")
        ctk.CTkButton(buttons, text="Save", width=100, command=self._save).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=100, fg_color="transparent", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        if loaded_questions:
            for draft in loaded_questions:
                self._add_row(draft)
        else:
            self._add_row(None)

        self.grab_set()
        self.lift()
        self._name.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        if hasattr(master, "bind_activity"):
            master.bind_activity(self)

    def _group_labels(self) -> list[str]:
        labels = [UNGROUPED]
        for group in self.session.db.list_groups():
            label = group_label(group.canonical_key, group.answer_kind)
            if label not in labels:
                labels.append(label)
        return labels

    def _add_row(self, draft: QuestionDraft | None) -> QuestionRow:
        frame = ctk.CTkFrame(self._questions)
        frame.pack(fill="x", padx=4, pady=6)
        frame.grid_columnconfigure(0, weight=1)
        question = ctk.CTkEntry(frame, placeholder_text="Security question")
        question.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(8, 4))
        delete = ctk.CTkButton(frame, text="Delete", width=110)
        delete.grid(row=0, column=3, padx=(4, 8), pady=(8, 4))
        answer = ctk.CTkEntry(frame, placeholder_text="Answer")
        answer.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 8))
        labels = self._group_labels()
        initial = UNGROUPED
        if draft is not None and draft.canonical_key:
            initial = group_label(draft.canonical_key, draft.answer_kind)
            if initial not in labels:
                labels.append(initial)
        group = ctk.CTkOptionMenu(frame, values=labels, width=220)
        group.set(initial)
        group.grid(row=1, column=1, padx=4, pady=(4, 8))
        generate = ctk.CTkButton(frame, text="Generate", width=110)
        generate.grid(row=1, column=2, padx=4, pady=(4, 8))
        new_group = ctk.CTkButton(frame, text="New group", width=110)
        new_group.grid(row=1, column=3, padx=(4, 8), pady=(4, 8))
        row = QuestionRow(
            frame=frame,
            question=question,
            answer=answer,
            group=group,
            generate=generate,
            question_id=None if draft is None else draft.question_id,
        )
        if draft is not None:
            if draft.question_text:
                question.insert(0, draft.question_text)
            if draft.answer_text:
                answer.insert(0, draft.answer_text)
        delete.configure(command=lambda current=row: self._delete_row(current))
        generate.configure(command=lambda current=row: self._generate(current))
        new_group.configure(command=lambda current=row: self._choose_new_group(current))
        self._rows.append(row)
        return row

    def _delete_row(self, row: QuestionRow) -> None:
        if not messagebox.askyesno("Delete", "Delete this question?", parent=self, default=messagebox.NO):
            return
        row.frame.destroy()
        if row in self._rows:
            self._rows.remove(row)

    def _selected_group(self, row: QuestionRow) -> tuple[str, str] | None:
        return parse_group_label(row.group.get())

    def _set_group(self, row: QuestionRow, key: str, kind: str) -> None:
        label = group_label(key, kind)
        labels = list(row.group.cget("values"))
        if label not in labels:
            labels.append(label)
            row.group.configure(values=labels)
        row.group.set(label)

    def _refresh_group_menus(self) -> None:
        labels = self._group_labels()
        for row in self._rows:
            current = row.group.get()
            values = list(labels)
            if current not in values:
                values.append(current)
            row.group.configure(values=values)
            row.group.set(current)

    def _choose_new_group(self, row: QuestionRow) -> None:
        question = row.question.get().strip()
        chosen = self._prompt_for_group(question or "question")
        if chosen is None or not self.winfo_exists():
            return
        self._set_group(row, chosen.canonical_key, chosen.answer_kind)
        self._status.configure(
            text=f"Using group {chosen.canonical_key}.",
            text_color=OK_COLOR,
        )

    def _generate(self, row: QuestionRow) -> None:
        if row.busy:
            return
        question = row.question.get().strip()
        if not question:
            self._set_status("Enter the question before generating an answer.", error=True)
            return
        row.busy = True
        row.generate.configure(state="disabled", text="Working...")
        selected = self._selected_group(row)
        if selected is not None:
            key, kind = selected
            self._apply(row, Classification(key, kind, "user"), question, f"Generated a unique answer in {key}.")
            self._end_generate(row)
            return
        classifier = Classifier(self.session.db, provider=None)
        try:
            local = classifier.classify_cached(normalize_question(question))
        except AppError as exc:
            self._set_status(str(exc), error=True)
            self._end_generate(row)
            return
        if local is not None:
            note = f"Grouped locally as {local.canonical_key}."
            self._apply(row, local, question, note)
            self._end_generate(row)
            return
        provider = provider_for(self.session.db)
        if provider is not None:
            groups = self.session.db.list_groups()

            def work() -> None:
                try:
                    raw = provider.classify(question, groups)
                    result = interpret_model_json(raw, groups)
                    error = None if result is not None else "The AI response could not be used."
                except AppError as exc:
                    result = None
                    error = str(exc)
                finally:
                    provider.close()
                invoke_on_main(
                    self,
                    lambda found=result, problem=error: self._after_remote(row, question, found, problem),
                )

            threading.Thread(target=work, daemon=True).start()
            return
        self._finish_without_ai(row, question, None)
        self._end_generate(row)

    def _after_remote(
        self,
        row: QuestionRow,
        question: str,
        result: Classification | None,
        error: str | None,
    ) -> None:
        if not self.winfo_exists() or not row.frame.winfo_exists():
            return
        self._finish_without_ai(row, question, error, remote=result)
        self._end_generate(row)

    def _finish_without_ai(
        self,
        row: QuestionRow,
        question: str,
        error: str | None,
        remote: Classification | None = None,
    ) -> None:
        result = remote if remote is not None else keyword_classification(question)
        if result is None:
            result = self._prompt_for_group(question)
            if result is None:
                self._set_status("Choose a question group before generating an answer.", error=True)
                return
        if result.source == "keyword" and error:
            note = f"AI unavailable ({error}). Grouped locally as {result.canonical_key}."
        elif result.source == "keyword":
            note = f"Grouped locally as {result.canonical_key}. Add an API key in Settings for AI grouping."
        elif result.source == "ai":
            note = (
                f"Grouped as {result.canonical_key}. The question text was sent to your AI provider. "
                "The answer was not."
            )
        else:
            note = f"Using group {result.canonical_key}."
        self._apply(row, result, question, note)

    def _apply(self, row: QuestionRow, classification: Classification, question: str, note: str) -> None:
        if not self.winfo_exists() or not row.frame.winfo_exists():
            return
        exclude_ids = {item.question_id for item in self._rows if item.question_id is not None}
        try:
            existing = self.session.db.normalized_answers(classification.canonical_key, exclude_ids)
            existing |= self._answers_in_form(classification.canonical_key)
            answer = generate_unique_answer(classification.answer_kind, existing)
        except AppError as exc:
            self._set_status(str(exc), error=True)
            return
        row.answer.delete(0, "end")
        row.answer.insert(0, answer)
        self._set_group(row, classification.canonical_key, classification.answer_kind)
        self._refresh_group_menus()
        try:
            self.session.db.save_classification(
                normalize_question(question),
                classification.canonical_key,
                classification.answer_kind,
                classification.source,
            )
            self.session.persist()
        except AppError as exc:
            self._set_status(f"Answer generated, but the vault could not be saved. {exc}", error=True)
            return
        self._set_status(note)

    def _answers_in_form(self, canonical_key: str) -> set[str]:
        found: set[str] = set()
        for row in self._rows:
            selected = self._selected_group(row)
            if selected is None or selected[0] != canonical_key:
                continue
            normalized = normalize_answer(row.answer.get())
            if normalized:
                found.add(normalized)
        return found

    def _end_generate(self, row: QuestionRow) -> None:
        row.busy = False
        if row.frame.winfo_exists():
            row.generate.configure(state="normal", text="Generate")

    def _prompt_for_group(self, question: str) -> Classification | None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Question group")
        dialog.geometry("460x280")
        dialog.transient(self)
        dialog.grab_set()
        dialog.lift()
        chosen: dict[str, Classification | None] = {"value": None}
        ctk.CTkLabel(
            dialog,
            text="This question could not be grouped automatically. Choose an answer type.",
            wraplength=400,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(16, 8))
        kind = ctk.CTkOptionMenu(dialog, values=list(ANSWER_KINDS), width=280)
        kind.pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(dialog, text="Group name").pack(anchor="w", padx=16, pady=(8, 0))
        key_entry = ctk.CTkEntry(dialog, width=280)
        key_entry.insert(0, slugify_question(question))
        key_entry.pack(anchor="w", padx=16, pady=4)
        error = ctk.CTkLabel(dialog, text="", text_color=ERROR_COLOR, wraplength=400)
        error.pack(anchor="w", padx=16, pady=4)

        def accept() -> None:
            key = key_entry.get().strip()
            if not is_canonical_key(key):
                error.configure(text="Use a short snake_case name that starts with a letter.")
                return
            selected_kind = kind.get()
            existing = self.session.db.kind_for_key(key)
            if existing is not None and existing != selected_kind:
                error.configure(text=f"The group '{key}' already uses the '{existing}' answer type.")
                return
            chosen["value"] = Classification(key, selected_kind if existing is None else existing, "user")
            dialog.destroy()

        ctk.CTkButton(dialog, text="Use this group", command=accept).pack(anchor="w", padx=16, pady=(8, 4))
        ctk.CTkButton(dialog, text="Cancel", fg_color="transparent", command=dialog.destroy).pack(anchor="w", padx=16)
        key_entry.focus_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self.wait_window(dialog)
        if self.winfo_exists():
            try:
                self.grab_set()
            except TclError:
                pass
        return chosen["value"]

    def _save(self) -> None:
        drafts: list[QuestionDraft] = []
        for row in self._rows:
            question = row.question.get().strip()
            answer = row.answer.get().strip()
            if not question and not answer:
                continue
            selected = self._selected_group(row)
            if selected is None:
                self._set_status("Choose a question group for every question. Generate does that for you.", error=True)
                return
            key, kind = selected
            drafts.append(QuestionDraft(question, answer, key, kind, row.question_id))
        try:
            self.account_id = self.session.db.save_account(self.account_id, self._name.get(), drafts)
            self.session.persist()
        except AppError as exc:
            self._set_status(str(exc), error=True)
            return
        self._on_saved()
        self.destroy()

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status.configure(text=message, text_color=ERROR_COLOR if error else OK_COLOR)
