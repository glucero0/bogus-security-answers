"""Readable CSV export and a check that the file still matches the vault."""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path

from bogus_security_answers.db import Database
from bogus_security_answers.errors import AppError, ValidationError
from bogus_security_answers.limits import MAX_CSV_BYTES

HEADERS = ("Account Name", "Question", "Answer", "Last Edited")
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class CsvRow:
    account_name: str
    question: str
    answer: str
    updated_at: str


@dataclass(frozen=True)
class Difference:
    account_name: str
    question: str
    vault_answer: str
    csv_answer: str
    vault_updated: str
    csv_updated: str


@dataclass
class VerifyReport:
    matched: int
    different: list[Difference]
    only_csv: list[tuple[str, str]]
    only_vault: list[tuple[str, str]]
    notes: list[str]

    def summary(self) -> str:
        lines = [
            f"Matched rows: {self.matched}",
            f"Different values: {len(self.different)}",
            f"Only in the CSV: {len(self.only_csv)}",
            f"Only in the vault: {len(self.only_vault)}",
            "",
        ]
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in self.notes[:100])
            lines.append("")
        if self.different:
            lines.append("Different values:")
            for item in self.different[:100]:
                label = item.question or "(no question)"
                lines.append(f"- {item.account_name} / {label}")
                lines.append(f"  Vault answer: {item.vault_answer}")
                lines.append(f"  CSV answer: {item.csv_answer}")
                lines.append(f"  Vault edited: {item.vault_updated}")
                lines.append(f"  CSV edited: {item.csv_updated}")
            lines.append("")
        if self.only_csv:
            lines.append("Only in the CSV:")
            for account_name, question in self.only_csv[:100]:
                lines.append(f"- {account_name} / {question or '(no question)'}")
            lines.append("")
        if self.only_vault:
            lines.append("Only in the vault:")
            for account_name, question in self.only_vault[:100]:
                lines.append(f"- {account_name} / {question or '(no question)'}")
            lines.append("")
        if self.matched and not self.different and not self.only_csv and not self.only_vault and not self.notes:
            lines.append("The CSV matches the vault.")
        return "\n".join(lines).rstrip() + "\n"


def rows_from_database(database: Database) -> list[CsvRow]:
    return [
        CsvRow(row.account_name, row.question_text, row.answer_text, row.updated_at)
        for row in database.search("")
    ]


def export_database(database: Database, destination: Path, vault_file: Path) -> int:
    path = Path(destination)
    _reject_reserved(path, vault_file)
    if path.exists() and path.is_dir():
        raise ValidationError("Choose a file, not a folder.")
    if not path.parent.is_dir():
        raise ValidationError("The folder for that CSV file does not exist.")
    rows = rows_from_database(database)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(
            [
                escape_cell(row.account_name),
                escape_cell(row.question),
                escape_cell(row.answer),
                escape_cell(row.updated_at),
            ]
        )
    payload = buffer.getvalue().encode("utf-8-sig")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    except OSError:
        raise AppError(f"The CSV file could not be written to {path.name}.") from None
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return len(rows)


def verify_csv(database: Database, path: Path) -> VerifyReport:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValidationError("Choose a CSV file to verify.")
    try:
        size = file_path.stat().st_size
    except OSError:
        raise ValidationError("The CSV file could not be read.") from None
    if size > MAX_CSV_BYTES:
        raise ValidationError("That CSV file is too large to verify.")
    try:
        text = file_path.read_text(encoding="utf-8-sig", newline="")
    except OSError:
        raise ValidationError("The CSV file could not be read.") from None
    except UnicodeError:
        raise ValidationError("The CSV file is not UTF-8 text.") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ValidationError("The CSV file is empty.") from None
    if tuple(cell.strip() for cell in header) != HEADERS:
        raise ValidationError(
            "The CSV header must be: Account Name, Question, Answer, Last Edited."
        )
    csv_rows: dict[tuple[str, str], CsvRow] = {}
    notes: list[str] = []
    for line_number, raw in enumerate(reader, start=2):
        if not raw or all(not cell.strip() for cell in raw):
            continue
        if len(raw) != 4:
            notes.append(f"Line {line_number} does not have 4 columns and was skipped.")
            continue
        account, question, answer, updated = (unescape_cell(cell) for cell in raw)
        if not account.strip():
            notes.append(f"Line {line_number} has no account name and was skipped.")
            continue
        key = (account.strip().casefold(), question.strip().casefold())
        if key in csv_rows:
            notes.append(
                f"Line {line_number} repeats {account.strip()} / {question.strip() or '(no question)'}. "
                "The last copy is used."
            )
        csv_rows[key] = CsvRow(account.strip(), question.strip(), answer, updated)
    vault_rows = {
        (row.account_name.strip().casefold(), row.question.strip().casefold()): row
        for row in rows_from_database(database)
    }
    matched = 0
    different: list[Difference] = []
    only_csv: list[tuple[str, str]] = []
    for key, csv_row in csv_rows.items():
        vault_row = vault_rows.get(key)
        if vault_row is None:
            only_csv.append((csv_row.account_name, csv_row.question))
            continue
        if vault_row.answer == csv_row.answer and vault_row.updated_at == csv_row.updated_at:
            matched += 1
        else:
            different.append(
                Difference(
                    account_name=vault_row.account_name,
                    question=vault_row.question,
                    vault_answer=vault_row.answer,
                    csv_answer=csv_row.answer,
                    vault_updated=vault_row.updated_at,
                    csv_updated=csv_row.updated_at,
                )
            )
    only_vault = [
        (row.account_name, row.question)
        for key, row in vault_rows.items()
        if key not in csv_rows
    ]
    return VerifyReport(matched, different, only_csv, only_vault, notes)


def escape_cell(value: str) -> str:
    if _dangerous(value):
        return "'" + value
    return value


def unescape_cell(value: str) -> str:
    if value.startswith("'") and _dangerous(value[1:]):
        return value[1:]
    return value


def _dangerous(value: str) -> bool:
    return value.lstrip(" \t").startswith(_DANGEROUS)


def _reject_reserved(path: Path, vault_file: Path) -> None:
    resolved = os.path.normcase(str(path.resolve()))
    reserved = {
        os.path.normcase(str(vault_file.resolve())),
        os.path.normcase(str(vault_file.resolve()) + ".bak"),
        os.path.normcase(str(vault_file.resolve()) + ".tmp"),
    }
    if resolved in reserved:
        raise ValidationError("Choose a different file. That path is the vault itself.")
