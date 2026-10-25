"""Encrypted vault file.

Layout: magic ``BSANSW1\\n``, a 4-byte big-endian header length, a UTF-8 JSON
header, then AES-256-GCM ciphertext. The header is the GCM associated data.
The key is Argon2id of the master password and is not stored in the file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bogus_security_answers.errors import PasswordError, UnlockError, VaultError
from bogus_security_answers.limits import (
    MAX_PASSWORD_LENGTH,
    MAX_VAULT_BYTES,
    MIN_PASSWORD_LENGTH,
)

MAGIC = b"BSANSW1\n"
MAX_HEADER_BYTES = 65536
UNLOCK_MESSAGE = (
    "Could not unlock the vault. The password may be wrong, or the file may be damaged."
)


@dataclass(frozen=True)
class KdfParams:
    time_cost: int = 3
    memory_cost: int = 65536
    parallelism: int = 1
    salt: bytes = b""

    def __post_init__(self) -> None:
        if (
            isinstance(self.time_cost, bool)
            or isinstance(self.memory_cost, bool)
            or isinstance(self.parallelism, bool)
            or not isinstance(self.time_cost, int)
            or not isinstance(self.memory_cost, int)
            or not isinstance(self.parallelism, int)
        ):
            raise VaultError("Invalid key derivation parameters.")
        if not 1 <= self.time_cost <= 10:
            raise VaultError("Invalid key derivation parameters.")
        if not 1 <= self.parallelism <= 8:
            raise VaultError("Invalid key derivation parameters.")
        if not 8 * self.parallelism <= self.memory_cost <= 1_048_576:
            raise VaultError("Invalid key derivation parameters.")
        if self.salt and not 16 <= len(self.salt) <= 64:
            raise VaultError("Invalid salt.")


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not password:
        raise PasswordError("Enter a master password.")
    if "\x00" in password:
        raise PasswordError("The password cannot contain a null character.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordError("That password is too long.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise PasswordError("The password contains characters that cannot be used.") from None


def derive_key(password: str, kdf: KdfParams) -> bytes:
    validate_password(password)
    if len(kdf.salt) < 16:
        raise VaultError("Invalid salt.")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=kdf.salt,
        time_cost=kdf.time_cost,
        memory_cost=kdf.memory_cost,
        parallelism=kdf.parallelism,
        hash_len=32,
        type=Type.ID,
    )


def keys_match(left: bytes, right: bytes) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def verify_password(password: str, key: bytes, kdf: KdfParams) -> bool:
    try:
        derived = derive_key(password, kdf)
    except PasswordError:
        return False
    return keys_match(derived, key)


class Vault:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def unlock(self, password: str) -> tuple[bytes, KdfParams, bytes]:
        validate_password(password)
        if not self.path.is_file():
            raise UnlockError("No vault was found.")
        try:
            data = self.path.read_bytes()
        except OSError:
            raise UnlockError(UNLOCK_MESSAGE) from None
        if len(data) > MAX_VAULT_BYTES + MAX_HEADER_BYTES:
            raise UnlockError(UNLOCK_MESSAGE)
        try:
            header_bytes, ciphertext = _split(data)
            header = json.loads(header_bytes.decode("utf-8"))
            kdf = _kdf_from_header(header)
            nonce = base64.b64decode(header["nonce"], validate=True)
            if len(nonce) != 12:
                raise UnlockError(UNLOCK_MESSAGE)
            key = derive_key(password, kdf)
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, header_bytes)
        except UnlockError:
            raise
        except PasswordError:
            raise
        except (InvalidTag, VaultError, ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            raise UnlockError(UNLOCK_MESSAGE) from None
        if len(plaintext) > MAX_VAULT_BYTES:
            raise UnlockError(UNLOCK_MESSAGE)
        return key, kdf, plaintext

    def write_encrypted(
        self,
        key: bytes,
        kdf: KdfParams,
        payload: bytes,
        *,
        refresh_backup: bool,
    ) -> None:
        if len(key) != 32:
            raise VaultError("The vault could not be saved.")
        if len(payload) > MAX_VAULT_BYTES:
            raise VaultError("The vault is too large to save.")
        blob = _seal(key, kdf, payload)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            backup = _sibling(self.path, ".bak")
            if refresh_backup:
                _atomic_write(backup, blob)
                _atomic_write(self.path, blob)
            else:
                if self.path.exists():
                    _atomic_write(backup, self.path.read_bytes())
                _atomic_write(self.path, blob)
        except OSError:
            raise VaultError("The vault file could not be saved.") from None


def _seal(key: bytes, kdf: KdfParams, payload: bytes) -> bytes:
    if len(kdf.salt) < 16:
        raise VaultError("Invalid salt.")
    nonce = secrets.token_bytes(12)
    header = {
        "version": 1,
        "kdf": "argon2id",
        "salt": base64.b64encode(kdf.salt).decode("ascii"),
        "time_cost": kdf.time_cost,
        "memory_cost": kdf.memory_cost,
        "parallelism": kdf.parallelism,
        "nonce": base64.b64encode(nonce).decode("ascii"),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise VaultError("The vault could not be saved.")
    ciphertext = AESGCM(key).encrypt(nonce, payload, header_bytes)
    return MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + ciphertext


def _split(data: bytes) -> tuple[bytes, bytes]:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 4:
        raise UnlockError(UNLOCK_MESSAGE)
    offset = len(MAGIC)
    header_len = int.from_bytes(data[offset : offset + 4], "big")
    offset += 4
    if header_len < 2 or header_len > MAX_HEADER_BYTES or offset + header_len > len(data):
        raise UnlockError(UNLOCK_MESSAGE)
    header_bytes = data[offset : offset + header_len]
    ciphertext = data[offset + header_len :]
    if not ciphertext:
        raise UnlockError(UNLOCK_MESSAGE)
    return header_bytes, ciphertext


def _kdf_from_header(header: object) -> KdfParams:
    if not isinstance(header, dict):
        raise UnlockError(UNLOCK_MESSAGE)
    if header.get("version") != 1 or header.get("kdf") != "argon2id":
        raise UnlockError(UNLOCK_MESSAGE)
    try:
        salt = base64.b64decode(header["salt"], validate=True)
        params = KdfParams(
            time_cost=header["time_cost"],
            memory_cost=header["memory_cost"],
            parallelism=header["parallelism"],
            salt=salt,
        )
    except (VaultError, KeyError, TypeError, ValueError):
        raise UnlockError(UNLOCK_MESSAGE) from None
    return params


def _sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = _sibling(path, ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _restrict(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _restrict(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def passwords_equal(left: str, right: str) -> bool:
    left_bytes = left.encode("utf-8")
    right_bytes = right.encode("utf-8")
    return hmac.compare_digest(
        hashlib.sha256(left_bytes).digest(),
        hashlib.sha256(right_bytes).digest(),
    )
