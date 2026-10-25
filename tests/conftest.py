import json
from pathlib import Path

import pytest

from caneta import config
from caneta.llm.base import Provider


class FakeProvider(Provider):
    """Canned replies chosen by a marker found in the prompt; records every prompt."""

    name = "fake"
    model = "fake-1"

    def __init__(self, replies: dict[str, object] | None = None, default: object = None):
        self.replies = replies or {}
        self.default = default if default is not None else {"findings": [], "parecer": "aprovar"}
        self.prompts: list[str] = []

    def _complete(self, prompt, system, json_mode, timeout, on_log):
        self.prompts.append(prompt)
        for marker, reply in self.replies.items():
            if marker in prompt:
                return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return self.default if isinstance(self.default, str) else json.dumps(self.default, ensure_ascii=False)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.caneta and projects folder."""
    home = tmp_path / "home"
    projects = tmp_path / "projects"
    monkeypatch.setattr(config, "APP_DIR", home)
    monkeypatch.setattr(config, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(config, "SECRETS_FILE", home / "secrets.json")
    monkeypatch.setattr(config, "_keyring", lambda: None)
    cfg = config.Config(projects_dir=str(projects), setup_done=True)
    config.save(cfg)
    return tmp_path


@pytest.fixture
def fake():
    return FakeProvider


@pytest.fixture
def sample_refs():
    return [
        {"id": "silva2023hidroides", "type": "article-journal", "title": "Hidroides do litoral de Sergipe",
         "author": [{"family": "Silva", "given": "Ana"}], "issued": {"date-parts": [[2023]]},
         "container-title": "Papéis Avulsos de Zoologia", "DOI": "10.1234/abc",
         "abstract": "Registramos 45 espécies de hidroides em Sergipe.", "caneta": {"source": "mcp", "verified": True}},
    ]


FIXTURES = Path(__file__).parent / "fixtures"
