"""Show the result of comparing a CSV file with the vault."""

from __future__ import annotations

import customtkinter as ctk

from bogus_security_answers.csv_io import VerifyReport


class VerifyDialog(ctk.CTkToplevel):
    def __init__(self, master, report: VerifyReport) -> None:
        super().__init__(master)
        self.title("Verify CSV")
        self.geometry("720x480")
        self.minsize(520, 320)
        self.transient(master)
        box = ctk.CTkTextbox(self, wrap="word")
        box.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        box.insert("1.0", report.summary())
        box.configure(state="disabled")
        close = ctk.CTkButton(self, text="Close", command=self.destroy)
        close.pack(pady=(0, 16))
        self.grab_set()
        self.lift()
        close.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())
