"""Portable checkpoints: one .caneta file (a zip of the project folder + caneta-checkpoint.json).

Works the same for both modes (imported article or from scratch). Import validates every member
(zip safety from ingest.py) and the sha256 manifest, then unpacks into a new project folder."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from . import __version__
from . import project as store
from .ingest import IngestError, extract_zip

FORMAT_VERSION = 1
MANIFEST = "caneta-checkpoint.json"
SKIP_DIRS = {".git", "__pycache__", "exports"}


class CheckpointError(Exception):
    pass


def _members(proj: store.Project, include_material: bool, include_pdfs: bool):
    for p in sorted(proj.path.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(proj.path)
        if rel.parts[0] in SKIP_DIRS or p.name.startswith(".") or p.suffix == ".tmp":
            continue
        if rel.parts[0] == "material" and not include_material:
            continue
        if p.suffix.lower() == ".pdf" and rel.parts[0] == "okf" and not include_pdfs:
            continue
        yield p, rel.as_posix()


def export(proj: store.Project, dest: Path, include_material: bool = True, include_pdfs: bool = False) -> Path:
    dest = Path(dest)
    files = {}
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path, rel in _members(proj, include_material, include_pdfs):
            data = path.read_bytes()
            files[rel] = hashlib.sha256(data).hexdigest()
            z.writestr(f"project/{rel}", data)
        manifest = {"format": FORMAT_VERSION, "app": "caneta", "app_version": __version__, "title": proj.title,
                    "mode": proj.mode, "created_at": store.now(), "files": files}
        z.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
    proj.log("checkpoint_exported", file=dest.name, files=len(files))
    return dest


def import_(src: Path, root: Path | None = None) -> store.Project:
    staging = Path(tempfile.mkdtemp(prefix="caneta-import-"))
    try:
        try:
            extract_zip(Path(src), staging)
        except IngestError as exc:
            raise CheckpointError(str(exc)) from exc
        try:
            manifest = json.loads((staging / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CheckpointError("arquivo .caneta sem manifesto válido") from exc
        if manifest.get("app") != "caneta" or int(manifest.get("format", 0)) > FORMAT_VERSION:
            raise CheckpointError("checkpoint de outra versão/formato; atualize a CANETA")
        base = staging / "project"
        for rel, digest in (manifest.get("files") or {}).items():
            p = base / rel
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise CheckpointError(f"arquivo corrompido ou ausente no checkpoint: {rel}")
        if not (base / store.PROJECT_FILE).is_file():
            raise CheckpointError("o checkpoint não contém um projeto")
        root = Path(root) if root else store.projects_root()
        slug = store._free_slug(root, manifest.get("title") or "manuscrito")
        shutil.move(str(base), str(root / slug))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    proj = store.load(slug, root)
    for sub in ("sections", "material", "okf", "reviews", "history"):
        (proj.path / sub).mkdir(exist_ok=True)
    proj.log("checkpoint_imported", source=Path(src).name)
    return proj
