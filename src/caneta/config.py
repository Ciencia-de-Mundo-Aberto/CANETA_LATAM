"""User settings (~/.caneta/config.json) and API keys (OS keyring, file fallback)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("CANETA_HOME", Path.home() / ".caneta"))
CONFIG_FILE = APP_DIR / "config.json"
SECRETS_FILE = APP_DIR / "secrets.json"  # only used when no OS keyring is available
KEYRING_SERVICE = "caneta"
LANGS = ("pt_BR", "es", "en")


def default_projects_dir() -> Path:
    return Path(os.environ.get("CANETA_PROJECTS", Path.home() / "Documents" / "CANETA"))


@dataclass
class Config:
    lang: str = "pt_BR"
    provider: str = "none"  # see llm.registry.PROVIDERS
    model: str = ""
    base_url: str = ""
    projects_dir: str = field(default_factory=lambda: str(default_projects_dir()))
    languagetool: str = "off"  # off | public | local
    languagetool_url: str = "http://localhost:8081/v2"
    semantic: bool = False  # meaning-based search with local embeddings (see semantic.py)
    setup_done: bool = False

    @property
    def projects_path(self) -> Path:
        return Path(self.projects_dir)


def load() -> Config:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save(cfg: Config) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


# ------------------------------------------------------------------ secrets
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - any keyring problem falls back to the file
        return None


def _file_secrets() -> dict:
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, name, value)
            return
        except Exception:  # noqa: BLE001
            pass
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = _file_secrets()
    data[name] = value
    SECRETS_FILE.write_text(json.dumps(data), encoding="utf-8")
    try:
        SECRETS_FILE.chmod(0o600)
    except OSError:
        pass


def get_secret(name: str) -> str | None:
    env = os.environ.get(f"CANETA_{name.upper()}_KEY")
    if env:
        return env
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, name)
            if value:
                return value
        except Exception:  # noqa: BLE001
            pass
    return _file_secrets().get(name)
