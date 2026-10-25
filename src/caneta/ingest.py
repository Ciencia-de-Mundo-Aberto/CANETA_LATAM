"""Author's reference material (results, notes, papers to cite) into <project>/material/.

Zip safety (path traversal, symlinks, zip bombs) is from lccv-paper-maker ingest.py.
PDFs are converted to Markdown next to the original (same converter as the MCP), so
the author's results can be checked when grounding claims. .bib/.ris/.json become references."""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from . import extras, references
from .project import Project, now, read_json, write_json

MAX_ZIP_BYTES = 500 * 1024 * 1024
MAX_ZIP_FILES = 5000
REFERENCE_EXT = {".bib", ".ris"}


class IngestError(Exception):
    pass


def safe_member(name: str) -> PurePosixPath:
    posix = PurePosixPath(name.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or (posix.parts and re.match(r"^[A-Za-z]:", posix.parts[0])):
        raise IngestError(f"entrada insegura no zip: {name!r}")
    return posix


def extract_zip(zip_path: Path, dest: Path, max_bytes: int = MAX_ZIP_BYTES) -> list[Path]:
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise IngestError(f"não é um zip válido: {Path(zip_path).name}") from exc
    written = []
    with archive:
        infos = archive.infolist()
        files = [i for i in infos if not i.is_dir()]
        for info in infos:
            safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise IngestError(f"o zip contém um link simbólico: {info.filename!r}")
        if len(files) > MAX_ZIP_FILES:
            raise IngestError(f"o zip tem mais de {MAX_ZIP_FILES} arquivos")
        if sum(i.file_size for i in files) > max_bytes:
            raise IngestError("o zip descompactado passa de 500 MB")
        total = 0
        for info in files:
            target = dest.joinpath(*safe_member(info.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > max_bytes:  # declared sizes can lie
                        raise IngestError("o zip descompactado passa de 500 MB")
                    out.write(chunk)
            written.append(target)
    return written


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_converter(pdf: Path, out: Path, mode: str, pages: int, timeout: int) -> bool:
    try:
        proc = subprocess.run([sys.executable, "-m", "latam_oa_mcp.convert", str(pdf), str(out), str(pages), mode],
                              capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and out.is_file()


def has_text(md_path: Path) -> bool:
    """Scanned PDFs come out of the normal conversion with (almost) no letters."""
    try:
        body = re.sub(r"<!--.*?-->|[*#|]", " ", md_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return len(re.findall(r"[^\W\d_]", body)) >= 500


def convert_pdf(pdf: Path, timeout: int = 240) -> tuple[Path, str] | None:
    """PDF → .md next to it (MCP converter in a subprocess; it prints to stdout).

    Born-digital PDFs (practically everything recent) convert directly. Only when that yields no text
    (a scanned PDF) and OCR is installed, the first 40 pages go through OCR (~10-15 s/page)."""
    out = pdf.with_suffix(".md")
    if _run_converter(pdf, out, "layout", 200, timeout) and has_text(out):
        return out, "pdf-layout"
    if extras.ocr_available() and _run_converter(pdf, out, "ocr", 40, 1200) and has_text(out):
        return out, "pdf-ocr"
    if out.is_file() and has_text(out):
        return out, "pdf-layout"
    out.unlink(missing_ok=True)
    return None


def add_material(project: Project, files: list[Path], convert: bool = True) -> dict:
    """Add uploaded files (any type; zips are extracted safely). Returns a summary."""
    folder = project.path / "material"
    folder.mkdir(exist_ok=True)
    manifest = read_json(folder / "manifest.json", [])
    added, refs_added, converted = [], [], 0
    for src in files:
        src = Path(src)
        if src.suffix.lower() == ".zip":
            staging = Path(tempfile.mkdtemp(prefix=".zip-", dir=folder))
            try:
                extracted = extract_zip(src, staging)
                dest = folder / _free(folder, src.stem)
                staging.rename(dest)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            paths = [dest / p.relative_to(staging) for p in extracted]
        else:
            dest = folder / _free(folder, src.name)
            shutil.copy2(src, dest)
            paths = [dest]
        for p in paths:
            entry = {"path": p.relative_to(project.path).as_posix(), "sha256": _sha(p), "added_at": now(),
                     "origin": src.name}
            if p.suffix.lower() == ".pdf" and convert:
                result = convert_pdf(p)
                if result:
                    entry["converted"] = result[0].relative_to(project.path).as_posix()
                    entry["method"] = result[1]
                    converted += 1
                else:
                    entry["scanned"] = True  # no text layer and no OCR: kept as PDF only
            if p.suffix.lower() in REFERENCE_EXT or (p.suffix.lower() == ".json" and "csl" in p.name.lower()):
                try:
                    items = references.import_bibtex(p) if p.suffix.lower() == ".bib" else \
                        references.import_ris_or_json(p)
                    refs, keys = references.add(project.references(), items)
                    project.save_references(refs)
                    refs_added += keys
                except Exception as exc:  # noqa: BLE001 - a bad .bib must not block the upload
                    entry["error"] = f"referências não importadas: {exc}"
            manifest.append(entry)
            added.append(entry)
    write_json(folder / "manifest.json", manifest)
    project.log("material_added", files=len(added), references=len(refs_added), converted_pdfs=converted)
    return {"files": added, "references": refs_added, "converted_pdfs": converted}


def _free(folder: Path, name: str) -> str:
    target, n = name, 2
    while (folder / target).exists():
        stem, suffix = Path(name).stem, Path(name).suffix
        target, n = f"{stem}-{n}{suffix}", n + 1
    return target


def list_material(project: Project) -> list[dict]:
    return read_json(project.path / "material" / "manifest.json", [])
