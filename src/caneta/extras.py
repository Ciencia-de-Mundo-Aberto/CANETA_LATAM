"""Optional features installed on demand into CANETA's own environment (never by default):

- OCR for scanned PDFs (rapidocr-onnxruntime, ~80 MB download). Born-digital PDFs never need it.
- Meaning-based search: multilingual embeddings (fastembed + a ~0.5 GB model), see semantic.py.

Packages are installed with uv into the running interpreter's environment (the uv tool venv
created by the installer). Re-running the installer replaces that environment; turn the
features on again afterwards (or install with the 'Incluir OCR' option)."""

from __future__ import annotations

import importlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

OCR_PACKAGES = ["rapidocr-onnxruntime>=1.4.4"]
OCR_SIZE = "≈80 MB"
SEMANTIC_PACKAGES = ["fastembed>=0.9"]
SEMANTIC_SIZE = "≈550 MB"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ExtraError(Exception):
    pass


def has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def ocr_available() -> bool:
    return has("rapidocr_onnxruntime") or has("rapidocr")


def uv_binary() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    for p in (home / ".local" / "bin" / "uv", home / ".local" / "bin" / "uv.exe",
              home / ".cargo" / "bin" / "uv", home / ".cargo" / "bin" / "uv.exe"):
        if p.is_file():
            return str(p)
    return None


def install(packages: list[str], log=print) -> None:
    """uv pip install into this interpreter's environment, streaming the output to `log`."""
    uv = uv_binary()
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, *packages]
    else:  # e.g. a plain venv with pip
        cmd = [sys.executable, "-m", "pip", "install", *packages]
    log("> " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=NO_WINDOW)
    for line in proc.stdout:
        log(line.rstrip())
    if proc.wait() != 0:
        raise ExtraError("a instalação falhou (veja o registro)")
    importlib.invalidate_caches()


def install_ocr(log=print) -> None:
    if not ocr_available():
        install(OCR_PACKAGES, log)
    if not ocr_available():
        raise ExtraError("o OCR foi instalado mas não pôde ser carregado")
