"""Where the encrypted vault lives on this computer."""

from __future__ import annotations

import os
from pathlib import Path


def vault_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "BogusSecurityAnswers" / "vault.db"
