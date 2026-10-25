"""An unlocked vault: the key stays in memory until lock."""

from __future__ import annotations

import secrets
from pathlib import Path

from bogus_security_answers.db import Database
from bogus_security_answers.errors import PasswordError, UnlockError
from bogus_security_answers.vault import KdfParams, Vault, derive_key, passwords_equal, validate_password, verify_password


def derive_new_key(password: str, kdf: KdfParams | None = None) -> tuple[bytes, KdfParams]:
    """Hash a new master password. This does not touch SQLite."""
    validate_password(password)
    base = kdf or KdfParams()
    params = KdfParams(base.time_cost, base.memory_cost, base.parallelism, secrets.token_bytes(16))
    return derive_key(password, params), params


class Session:
    def __init__(self, vault: Vault, key: bytes, kdf: KdfParams, database: Database) -> None:
        self.vault = vault
        self.key = key
        self.kdf = kdf
        self.db = database

    @classmethod
    def create(cls, path: Path, password: str, kdf: KdfParams | None = None) -> Session:
        key, params = derive_new_key(password, kdf)
        return cls.from_new_key(path, key, params)

    @classmethod
    def from_new_key(cls, path: Path, key: bytes, kdf: KdfParams) -> Session:
        vault = Vault(path)
        if vault.exists():
            raise UnlockError("A vault already exists. Enter its master password to unlock it.")
        database = Database.create_empty()
        session = cls(vault, key, kdf, database)
        session.persist()
        return session

    @classmethod
    def from_payload(cls, path: Path, key: bytes, kdf: KdfParams, payload: bytes) -> Session:
        database = Database.from_bytes(payload)
        return cls(Vault(path), key, kdf, database)

    @classmethod
    def unlock(cls, path: Path, password: str) -> Session:
        vault = Vault(path)
        key, params, payload = vault.unlock(password)
        database = Database.from_bytes(payload)
        return cls(vault, key, params, database)

    def persist(self) -> None:
        payload = self.db.serialize()
        self.vault.write_encrypted(self.key, self.kdf, payload, refresh_backup=False)

    def change_password(self, current: str, new: str) -> None:
        validate_password(new)
        if not verify_password(current, self.key, self.kdf):
            raise UnlockError("The current password is wrong.")
        if passwords_equal(current, new):
            raise PasswordError("Choose a different password.")
        params = KdfParams(
            self.kdf.time_cost,
            self.kdf.memory_cost,
            self.kdf.parallelism,
            secrets.token_bytes(16),
        )
        key = derive_key(new, params)
        payload = self.db.serialize()
        self.vault.write_encrypted(key, params, payload, refresh_backup=True)
        self.key = key
        self.kdf = params

    def lock(self) -> None:
        self.key = b""
        self.db.close()
