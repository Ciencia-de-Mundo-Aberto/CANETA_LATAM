"""Build the shareable release folder:

    uv run python scripts/build_release.py

releases/CANETA-<version>/
  CANETA-Instalador-Windows.exe     ← clique duplo (Windows)
  CANETA-<version>-windows.zip      ← install.bat + install.ps1
  CANETA-<version>-linux.zip        ← install.sh
  CANETA-<version>-macos.zip        ← "Instalar CANETA.command" + install.sh
  windows/  linux/  macos/          ← the same, unzipped
  LEIA-ME.txt

Every installer downloads CANETA from GitHub at tag v<version> (push the tag before sharing). This script
refuses to build when an installer points at another tag. The .exe is built only on Windows (PyInstaller
does not cross-compile)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
INSTALLERS = {"install.ps1": r'else \{ "(v[^"]+)" \}', "install.sh": r'CANETA_REF:-(v[^}]+)\}',
              "packaging/installer_gui.py": r'REF = "(v[^"]+)"'}


def sh(*cmd: str) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def write_text(path: Path, text: str, crlf: bool = False, executable: bool = False) -> None:
    text = text.replace("\r\n", "\n")
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    if executable:
        path.chmod(0o755)


def zip_dir(folder: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                info = zipfile.ZipInfo.from_file(p, Path("CANETA") / p.relative_to(folder))
                if p.suffix in (".sh", ".command"):
                    info.external_attr = (0o755 << 16)  # keep scripts executable on macOS/Linux
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())


README = """CANETA {version} - Central de Assistência e Navegação para Escrita de Textos Avançados
Assistente aberto de escrita científica, seção por seção, focado em revistas Diamond Open Access.

COMO INSTALAR (precisa de internet; leva de 1 a 3 minutos; a CANETA é baixada do GitHub)

  Windows (mais fácil): clique duas vezes em CANETA-Instalador-Windows.exe
      Se o Windows mostrar "O Windows protegeu o computador": clique em "Mais informações" > "Executar assim mesmo"
      (o instalador ainda não tem assinatura digital paga).
  Windows (alternativa): descompacte CANETA-{version}-windows.zip e clique duas vezes em install.bat

  macOS: descompacte CANETA-{version}-macos.zip e clique duas vezes em "Instalar CANETA.command"
      Se o macOS bloquear: botão direito no arquivo > Abrir > Abrir.

  Linux: descompacte CANETA-{version}-linux.zip e rode no terminal:  sh install.sh

DEPOIS DE INSTALAR
  A CANETA abre no navegador. Para abrir de novo: Menu Iniciar > CANETA (Windows) ou o comando  caneta
  A janela/terminal do servidor precisa ficar aberta enquanto você escreve; feche-a para encerrar.
  Tudo roda no seu computador. Os manuscritos ficam em Documentos/CANETA.

DESINSTALAR
  uv tool uninstall caneta

Código: https://github.com/LucaswasTaken/CANETA_LATAM
AGPL-3.0 (código) · CC BY 4.0 (textos) · Leia o MANIFESTO.md
"""


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    out = ROOT / "releases" / f"CANETA-{version}"
    build = ROOT / "build" / "release"
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    for name, pattern in INSTALLERS.items():
        ref = re.search(pattern, (ROOT / name).read_text(encoding="utf-8")).group(1)
        if ref != f"v{version}":
            sys.exit(f"{name} installs {ref}, but the version is {version}: update it first")
    out.mkdir(parents=True)

    # 1) per-platform folders (the installers download CANETA from GitHub)
    for plat in ("windows", "linux", "macos"):
        folder = out / plat
        folder.mkdir()
        write_text(folder / "LEIA-ME.txt", README.format(version=version), crlf=(plat == "windows"))
    write_text(out / "windows" / "install.ps1", (ROOT / "install.ps1").read_text(encoding="utf-8"), crlf=True)
    write_text(out / "windows" / "install.bat", (PACKAGING / "install.bat").read_text(encoding="utf-8"), crlf=True)
    write_text(out / "linux" / "install.sh", (ROOT / "install.sh").read_text(encoding="utf-8"), executable=True)
    write_text(out / "macos" / "install.sh", (ROOT / "install.sh").read_text(encoding="utf-8"), executable=True)
    write_text(out / "macos" / "Instalar CANETA.command", (PACKAGING / "install.command").read_text(encoding="utf-8"),
               executable=True)
    write_text(out / "LEIA-ME.txt", README.format(version=version))
    for plat in ("windows", "linux", "macos"):
        zip_dir(out / plat, out / f"CANETA-{version}-{plat}.zip")

    # 2) the Windows .exe (graphical installer)
    if os.name == "nt":
        ico = build / "caneta.ico"
        sh("uv", "run", "--with", "pillow", "python", str(PACKAGING / "make_icon.py"), str(ico))
        sep = ";"
        sh("uv", "run", "--with", "pyinstaller", "pyinstaller", "--noconfirm", "--onefile", "--windowed",
           "--name", "CANETA-Instalador-Windows", "--icon", str(ico),
           "--add-data", f"{ico}{sep}.",
           "--distpath", str(out), "--workpath", str(build / "pyi"), "--specpath", str(build / "pyi"),
           str(PACKAGING / "installer_gui.py"))
    else:
        print("(.exe skipped: build it on Windows)", file=sys.stderr)

    print("\nRelease:", out)
    for p in sorted(out.iterdir()):
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        print(f"  {p.name:40} {size / 1e6:6.1f} MB")


if __name__ == "__main__":
    main()
