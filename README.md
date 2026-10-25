# Security Answers

Security Answers is a Windows desktop app for storing the answers to online security questions. It keeps those answers in a small vault on this computer. The vault is encrypted with a master password, and the app opens straight to that password window.

Answers come from [Faker](https://faker.readthedocs.io/), the Python library that corresponds to the .NET Bogus package. Faker produces plausible values such as `Boise, ID`, a last name, or a street. The app checks the vault and will not reuse an answer inside the same question group. `Boise` and `Boise, ID` count as the same answer.

OpenRouter or Gemini is used only to decide whether a new question means the same thing as one you already stored, and which kind of Faker value it needs. The model does not invent the answer.

## Install

Install Python 3.12 or newer from the Windows installer, so tkinter and sqlite3 are included. Then, in this folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
```

That installs this app from `pyproject.toml`. The `[dev]` extra adds pytest.

`requirements.txt` lists the same runtime packages, with the same version pins, for CodeQL and Dependabot:

- `argon2-cffi` hashes the master password.
- `cryptography` encrypts the vault.
- `customtkinter` draws the window.
- `Faker` generates the answers.
- `httpx` calls OpenRouter and Gemini.
- `rapidfuzz` compares similar questions on this computer.

`requirements-dev.txt` includes `requirements.txt` and pytest. setuptools is pinned under `[build-system]` in `pyproject.toml` and is fetched when the app is installed.

## Run

```powershell
py -m bogus_security_answers
```

`run.bat` starts the same app without a console window. The vault file is:

`%LOCALAPPDATA%\BogusSecurityAnswers\vault.db`

Unlocking takes a moment. The master password is hashed with Argon2id (64 MiB, 3 iterations) before the vault is decrypted.

## Master password

The first launch asks you to create a master password of at least 12 characters. There is no password reset. If you forget this password, the vault cannot be opened.

The window locks after 5 minutes without keyboard or mouse activity. Change that interval in Settings, or click Lock. You can also change the master password in Settings. A second copy of the app will not open while the first one is running.

## Accounts

- Add Account, Edit Account, and Delete Account manage the account and its questions.
- Inside an account, Add creates a question, Delete removes that question, and Save writes the account.
- Generate fills in a unique answer. Pick New group when you want to choose the answer type yourself.
- The account list shows one row per account, with the account name and the last time it was edited. Questions and answers stay inside Edit Account.
- Search looks through account names, questions, and answers. Matches are shown with the question and answer. Clear the box and click Search to return to the account list. Edit and Delete use the selected row.

## How a question is grouped

1. An exact question already seen on this computer is reused, with no network call.
2. A very similar cached question is reused locally.
3. Otherwise the selected AI provider receives the question text and the names of existing groups. It must answer with JSON that picks an allowlisted answer type. Answers, account names, and the API key are not included.
4. If there is no API key, or the provider cannot be reached, a short list of keywords is used.
5. If that still does not match, you choose the group.

Settings stores the provider (OpenRouter or Gemini), the model name, and the API key inside the vault. Test connection checks the key and does not store a sample answer. Settings also shows the full path of the vault file, and Copy path puts that path on the clipboard.

## CSV

Export CSV writes one row per question:

`Account Name, Question, Answer, Last Edited`

An account with no questions is still included, with the question and answer left blank. The file is UTF-8 with a byte order mark so Excel and Google Sheets can open it. The export is plaintext on purpose, so you can read it on another device. Anyone who can open the file can read the answers.

A value that could be an Excel formula (it starts with `=`, `+`, `-`, `@`, a tab, or a carriage return) is prefixed with `'` so the spreadsheet shows it as text. Verify CSV strips that prefix and compares the file with the open vault. It reports rows that match, values that differ, rows that exist only in the CSV, and rows that exist only in the vault.

## What the master password protects

- The vault file and its one backup, `vault.db.bak`, are AES-256-GCM ciphertext. Copying the file to another computer does not reveal the accounts.
- After you unlock, the accounts are in memory until you lock the app or it locks itself.
- The CSV export is a separate plaintext copy, created only when you export.
- Question text is sent to OpenRouter or Gemini only for a question that is not already grouped locally.
- A weak or forgotten password is the main risk. There is no recovery file and no password hint.

## Backup

Copy the vault file shown in Settings while the app is closed. That file is already encrypted. To restore, put it back in the same folder and unlock it with the same master password. Keep the master password in a password manager, separate from the vault file.

`vault.db.bak`, in the same folder, is only the previous save on this computer. It does not help if the disk or that folder is lost. After a master-password change, older copies of `vault.db` still open with the old password.

Export CSV is a readable copy of account names, questions, and answers. It is plaintext, and the app cannot import it. Use it to read the answers somewhere else, not as the only backup.

## Tests

```powershell
py -m pytest
```
