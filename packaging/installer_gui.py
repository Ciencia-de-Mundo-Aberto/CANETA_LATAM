"""CANETA — instalador gráfico para Windows (empacotado como .exe pelo PyInstaller).

É pequeno: baixa a CANETA direto do GitHub (tag REF). Ao clicar em Instalar:
  1. instala o uv (instalador oficial da Astral) se ele ainda não existir;
  2. instala a CANETA do repositório no GitHub (nunca pelo nome: no PyPI existe outro pacote 'caneta'),
     com OCR opcional (caixa desmarcada por padrão; PDFs recentes não precisam dele) e busca por
     significado opcional (marcada quando já estava ativa, para atualizações não a perderem);
  3. cria atalhos no Menu Iniciar e na Área de Trabalho;
  4. abre a CANETA no navegador.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk

BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
REPO = "https://github.com/Ciencia-de-Mundo-Aberto/CANETA_LATAM"
REF = "v0.1.10"  # kept equal to the project version by scripts/build_release.py
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BRAND, ACCENT = "#123c3a", "#f2c14e"


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    home = Path.home()
    for p in (home / ".local" / "bin" / "uv.exe", home / ".cargo" / "bin" / "uv.exe"):
        if p.is_file():
            return str(p)
    return None


def semantic_was_on() -> bool:
    """An update must keep meaning-based search when the author had turned it on (reinstalling the tool
    environment drops its library; the model itself stays in ~/.caneta/models)."""
    cfg = Path(os.environ.get("CANETA_HOME", Path.home() / ".caneta")) / "config.json"
    try:
        return bool(json.loads(cfg.read_text(encoding="utf-8")).get("semantic"))
    except (OSError, ValueError):
        return False


class Installer:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Instalador da CANETA")
        root.geometry("660x540")
        root.configure(bg="white")
        try:
            root.iconbitmap(str(BUNDLE / "caneta.ico"))
        except tk.TclError:
            pass
        head = tk.Frame(root, bg=BRAND)
        head.pack(fill="x")
        tk.Label(head, text="CANETA", fg="white", bg=BRAND, font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=18,
                                                                                               pady=(14, 0))
        tk.Label(head, text="Central de Assistência e Navegação para Escrita de Textos Avançados", fg="#cfe7e1",
                 bg=BRAND, font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(0, 14))
        body = tk.Frame(root, bg="white")
        body.pack(fill="both", expand=True, padx=18, pady=12)
        tk.Label(body, bg="white", justify="left", wraplength=600, font=("Segoe UI", 10), text=(
            "Assistente aberto de escrita científica, seção por seção, com citações de revistas "
            "Diamond Open Access (SciELO, DOAJ).\n\nO instalador vai baixar o uv (que traz o Python) e as bibliotecas "
            "necessárias, por isso precisa de internet. Leva de 1 a 3 minutos.")).pack(anchor="w")
        options = tk.Frame(body, bg="white")
        options.pack(fill="x", pady=(10, 0))
        self.semantic = tk.BooleanVar(value=semantic_was_on())
        tk.Checkbutton(options, variable=self.semantic, bg="white", anchor="w", justify="left", text=(
            "Busca por significado (+≈550 MB): sugestões de revista bem melhores para textos ensaísticos ou "
            "interdisciplinares. Pode ser ativada depois, em Configurações."), wraplength=600).pack(fill="x")
        self.ocr = tk.BooleanVar(value=False)
        tk.Checkbutton(options, variable=self.ocr, bg="white", anchor="w", text=(
            "OCR para PDFs digitalizados (+≈80 MB). PDFs digitais comuns não precisam.")).pack(fill="x")
        self.desktop = tk.BooleanVar(value=True)
        tk.Checkbutton(options, text="Atalho na Área de Trabalho", variable=self.desktop, bg="white",
                       anchor="w").pack(fill="x")
        self.progress = ttk.Progressbar(body, mode="determinate", maximum=6)
        self.progress.pack(fill="x", pady=(12, 6))
        self.status = tk.Label(body, text="Pronto para instalar.", bg="white", font=("Segoe UI", 10, "bold"))
        self.status.pack(anchor="w")
        self.log = scrolledtext.ScrolledText(body, height=9, font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, pady=6)
        buttons = tk.Frame(body, bg="white")
        buttons.pack(fill="x")
        self.button = tk.Button(buttons, text="Instalar e abrir", bg=ACCENT, fg="#1b2624", relief="flat",
                                font=("Segoe UI", 11, "bold"), padx=16, pady=6, command=self.start)
        self.button.pack(side="right")
        tk.Button(buttons, text="Fechar", relief="flat", padx=12, pady=6, command=root.destroy).pack(side="right",
                                                                                                   padx=8)

    # ------------------------------------------------------------------ ui helpers (thread-safe)
    def say(self, text: str) -> None:
        def add():
            self.log.configure(state="normal")
            self.log.insert("end", text.rstrip() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, add)

    def step(self, n: int, text: str) -> None:
        self.root.after(0, lambda: (self.progress.configure(value=n), self.status.configure(text=text)))

    def run(self, cmd: list[str]) -> int:
        self.say("> " + " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        for line in proc.stdout:
            self.say(line)
        return proc.wait()

    # ------------------------------------------------------------------ steps
    def start(self) -> None:
        self.button.configure(state="disabled")
        threading.Thread(target=self.install, daemon=True).start()

    def install(self) -> None:
        try:
            self.step(1, "Procurando o uv…")
            uv = find_uv()
            if not uv:
                self.step(1, "Instalando o uv (instalador oficial da Astral)…")
                code = self.run(["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command",
                                 "irm https://astral.sh/uv/install.ps1 | iex"])
                uv = find_uv()
                if code != 0 or not uv:
                    raise RuntimeError("não foi possível instalar o uv (verifique a internet)")

            self.step(2, f"Baixando a CANETA {REF} do GitHub e instalando (1–3 min)…")
            extra = ["--with", "rapidocr-onnxruntime>=1.4.4"] if self.ocr.get() else []
            if self.semantic.get():
                extra += ["--with", "fastembed>=0.9"]
            code = self.run([uv, "tool", "install", "--python", "3.12", "--force", *extra,
                             f"caneta @ git+{REPO}@{REF}"])
            if code != 0:
                raise RuntimeError("a instalação da CANETA falhou (veja o registro acima)")
            self.run([uv, "tool", "update-shell"])
            bin_dir = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True,
                                     creationflags=NO_WINDOW).stdout.strip()
            exe = Path(bin_dir) / "caneta.exe"

            if self.semantic.get():
                self.step(3, "Baixando o modelo da busca por significado (≈0,5 GB, só na primeira vez)…")
                if self.run([str(exe), "--enable-semantic"]) != 0:  # not fatal: it can be turned on later
                    self.say("A busca por significado não foi ativada; tente depois em Configurações.")

            self.step(4, "Criando atalhos…")
            self.shortcuts(exe)

            self.step(5, "Abrindo a CANETA…")
            subprocess.Popen([str(exe)], creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
            self.step(6, "Pronto! A CANETA abriu no navegador. Para abrir depois: Menu Iniciar › CANETA.")
            self.say("\nInstalação concluída. A janela preta que abriu é o servidor local da CANETA: "
                     "deixe-a aberta enquanto escreve e feche-a para encerrar.")
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.step(0, f"Erro: {exc}")
            self.say(f"ERRO: {exc}")
            self.root.after(0, lambda: self.button.configure(state="normal", text="Tentar de novo"))

    def shortcuts(self, exe: Path) -> None:
        targets = [Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"]
        if self.desktop.get():
            targets.append(Path.home() / "Desktop")
        icon = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CANETA" / "caneta.ico"
        icon.parent.mkdir(parents=True, exist_ok=True)
        if (BUNDLE / "caneta.ico").is_file():
            shutil.copy2(BUNDLE / "caneta.ico", icon)
        for folder in targets:
            if not folder.is_dir():
                continue
            lnk = folder / "CANETA.lnk"
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
                  f"$s.TargetPath='{exe}';$s.IconLocation='{icon}';"
                  "$s.Description='CANETA - escrita cientifica aberta';$s.Save()")
            self.run(["powershell", "-NoProfile", "-ExecutionPolicy", "ByPass", "-Command", ps])


def main() -> None:
    root = tk.Tk()
    Installer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
