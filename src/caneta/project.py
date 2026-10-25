"""A CANETA project: one folder per manuscript.

    project.yaml        title, lang, mode (A = imported article | B = from scratch), journal, sections
    sections/NN-slug.md one file per section (body only; the heading comes from project.yaml)
    references.json     CSL-JSON (citations are data)
    material/           author's reference material
    okf/                OKF bundle written by the latam-oa-mcp tools
    reviews/<id>/       review rounds per section
    history/<id>/       snapshot before every change ("error is a state, not a sin")
    log.jsonl           events, including every AI call (provider, model, prompt hash)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import config
from .sections import IMRAD, KINDS, STATES, StateError, guess_kind, slugify

PROJECT_FILE = "project.yaml"


class ProjectError(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


@dataclass
class Section:
    id: str
    title: str
    kind: str
    state: str
    file: str

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind, "state": self.state, "file": self.file}


class Project:
    def __init__(self, path: Path):
        self.path = Path(path)
        data = yaml.safe_load((self.path / PROJECT_FILE).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or "title" not in data:
            raise ProjectError(f"invalid {PROJECT_FILE} in {self.path}")
        self.data = data

    # ------------------------------------------------------------------ basics
    @property
    def slug(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return self.data["title"]

    @property
    def lang(self) -> str:
        return self.data.get("lang", "pt_BR")

    @property
    def mode(self) -> str:
        return self.data.get("mode", "A")

    @property
    def journal(self) -> dict | None:
        return self.data.get("journal")

    def save(self) -> None:
        self.data["updated_at"] = now()
        atomic_write(self.path / PROJECT_FILE, yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True))

    def log(self, event: str, **fields) -> None:
        line = json.dumps({"at": now(), "event": event, **fields}, ensure_ascii=False)
        with (self.path / "log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def events(self, limit: int = 200) -> list[dict]:
        try:
            lines = (self.path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def log_ai(self, purpose: str, provider: str, model: str, prompt: str, ok: bool, section: str | None = None,
               duration_s: float | None = None) -> None:
        """Transparency: every AI call is recorded (never the text itself, only its hash)."""
        self.log("ai_call", purpose=purpose, provider=provider, model=model, section=section, ok=ok,
                 prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(), duration_s=duration_s)

    # ------------------------------------------------------------------ sections
    @property
    def sections(self) -> list[Section]:
        return [Section(**s) for s in self.data.get("sections", [])]

    def section(self, section_id: str) -> Section:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise ProjectError(f"section not found: {section_id}")

    def _set_sections(self, sections: list[Section]) -> None:
        self.data["sections"] = [s.as_dict() for s in sections]
        self.save()

    def read_section(self, section_id: str) -> str:
        sec = self.section(section_id)
        try:
            return (self.path / sec.file).read_text(encoding="utf-8")
        except OSError:
            return ""

    def snapshot(self, section_id: str, reason: str) -> Path | None:
        """Copy the current text to history/<id>/ before changing it."""
        text = self.read_section(section_id)
        if not text.strip():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        target = self.path / "history" / section_id / f"{stamp}-{slugify(reason, 24)}.md"
        atomic_write(target, text)
        return target

    def history(self, section_id: str) -> list[dict]:
        folder = self.path / "history" / section_id
        if not folder.is_dir():
            return []
        items = []
        for p in sorted(folder.glob("*.md"), reverse=True):
            stamp, _, reason = p.stem.partition("-")
            items.append({"file": p.name, "stamp": stamp, "reason": reason, "chars": p.stat().st_size})
        return items

    def write_section(self, section_id: str, text: str, reason: str, force: bool = False) -> None:
        sec = self.section(section_id)
        if sec.state == "approved" and not force:
            raise StateError("section approved: reopen it before editing")
        if text == self.read_section(section_id):
            return
        self.snapshot(section_id, reason)
        atomic_write(self.path / sec.file, text.replace("\r\n", "\n"))
        self.log("section_written", section=section_id, reason=reason, chars=len(text))

    def restore(self, section_id: str, history_file: str) -> None:
        source = self.path / "history" / section_id / Path(history_file).name
        if not source.is_file():
            raise ProjectError("snapshot not found")
        self.write_section(section_id, source.read_text(encoding="utf-8"), f"restaurado {source.stem[:15]}")

    def set_state(self, section_id: str, state: str) -> None:
        if state not in STATES:
            raise ProjectError(f"invalid state: {state}")
        sections = self.sections
        for s in sections:
            if s.id == section_id:
                old, s.state = s.state, state
        self._set_sections(sections)
        self.log("section_state", section=section_id, state=state, previous=old)

    def add_section(self, title: str, kind: str | None = None, text: str = "", position: int | None = None) -> Section:
        title = title.strip() or "Nova seção"
        kind = kind if kind in KINDS else guess_kind(title)
        sections = self.sections
        sid = f"s{secrets.token_hex(3)}"
        file = f"sections/{sid}-{slugify(title)}.md"
        sec = Section(id=sid, title=title, kind=kind, state="draft", file=file)
        atomic_write(self.path / file, text)
        if position is None or position >= len(sections):
            sections.append(sec)
        else:
            sections.insert(max(0, position), sec)
        self._set_sections(sections)
        self.log("section_added", section=sid, title=title, kind=kind)
        return sec

    def update_section_meta(self, section_id: str, title: str | None = None, kind: str | None = None) -> None:
        sections = self.sections
        for s in sections:
            if s.id == section_id:
                if title and title.strip():
                    s.title = title.strip()
                if kind in KINDS:
                    s.kind = kind
        self._set_sections(sections)

    def move_section(self, section_id: str, delta: int) -> None:
        sections = self.sections
        idx = next(i for i, s in enumerate(sections) if s.id == section_id)
        new = max(0, min(len(sections) - 1, idx + delta))
        sections.insert(new, sections.pop(idx))
        self._set_sections(sections)

    def delete_section(self, section_id: str) -> None:
        """The text is kept in history/<id>/ (nothing is ever lost)."""
        self.snapshot(section_id, "excluida")
        sections = [s for s in self.sections if s.id != section_id]
        sec = self.section(section_id)
        (self.path / sec.file).unlink(missing_ok=True)
        self._set_sections(sections)
        self.log("section_deleted", section=section_id, title=sec.title)

    def next_open_section(self) -> Section | None:
        return next((s for s in self.sections if s.state != "approved"), None)

    # ------------------------------------------------------------------ references
    @property
    def references_file(self) -> Path:
        return self.path / "references.json"

    def references(self) -> list[dict]:
        data = read_json(self.references_file, [])
        return data if isinstance(data, list) else []

    def save_references(self, refs: list[dict]) -> None:
        write_json(self.references_file, refs)


# ---------------------------------------------------------------------- store
def projects_root() -> Path:
    root = config.load().projects_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def _free_slug(root: Path, title: str) -> str:
    base = slugify(title, 40) or "manuscrito"
    slug, n = base, 2
    while (root / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create(title: str, lang: str = "pt_BR", mode: str = "B", structure: str = "imrad",
           root: Path | None = None) -> Project:
    """mode A = the author brings an article (sections added by the importer);
    mode B = from scratch (structure: imrad | empty)."""
    root = Path(root) if root else projects_root()
    path = root / _free_slug(root, title)
    for sub in ("sections", "material", "okf", "reviews", "history"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    data = {"title": title.strip() or "Manuscrito", "lang": lang if lang in config.LANGS else "pt_BR",
            "mode": mode, "journal": None, "csl": "apa", "created_at": now(), "updated_at": now(),
            "caneta_version": _version(), "sections": []}
    atomic_write(path / PROJECT_FILE, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    write_json(path / "references.json", [])
    project = Project(path)
    project.log("project_created", mode=mode)
    if mode == "B" and structure == "imrad":
        for title_, kind in IMRAD.get(project.lang, IMRAD["pt_BR"]):
            project.add_section(title_, kind)
    return project


def load(slug: str, root: Path | None = None) -> Project:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        raise ProjectError("invalid project id")
    path = (Path(root) if root else projects_root()) / slug
    if not (path / PROJECT_FILE).is_file():
        raise ProjectError(f"project not found: {slug}")
    return Project(path)


def list_projects(root: Path | None = None) -> list[Project]:
    root = Path(root) if root else projects_root()
    out = []
    for p in sorted(root.iterdir()) if root.exists() else []:
        if (p / PROJECT_FILE).is_file():
            try:
                out.append(Project(p))
            except (ProjectError, yaml.YAMLError):
                continue
    return sorted(out, key=lambda pr: pr.data.get("updated_at", ""), reverse=True)


def _version() -> str:
    from . import __version__

    return __version__
