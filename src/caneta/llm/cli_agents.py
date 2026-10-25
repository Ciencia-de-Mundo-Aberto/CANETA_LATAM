"""Claude CLI and Codex CLI as plain text completers (adapted from lccv-paper-maker agent.py).

Inside CANETA the CLIs run WITHOUT tools and without MCP, in an empty temporary folder:
CANETA itself searches the sources and puts them in the prompt, so we know exactly what
the model saw. The prompt goes through stdin (never a shell)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from .base import Provider

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
INSTALL_HINTS = {
    "claude": "Claude CLI não encontrado. Instale o Claude Code (https://claude.com/claude-code).",
    "codex": "Codex CLI não encontrado. Instale com `npm install -g @openai/codex`.",
}


def _known_dirs(agent: str) -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        dirs = [Path(os.environ["APPDATA"]) / "npm"] if os.environ.get("APPDATA") else []
        dirs += [home / ".local" / "bin", home / ".claude" / "local"]
        ext = home / ".vscode" / "extensions"
        if agent == "claude" and ext.is_dir():
            dirs += sorted(ext.glob("anthropic.claude-code-*/resources/native-binary"), reverse=True)
        return dirs
    return [home / ".local" / "bin", home / ".claude" / "local", Path("/usr/local/bin"), Path("/opt/homebrew/bin")]


def find_binary(agent: str) -> str | None:
    """PATH first, then known install locations (claude is often missing from the PATH)."""
    found = shutil.which(agent)
    if found:
        return str(Path(found).resolve())
    for folder in _known_dirs(agent):
        if folder.is_dir():
            found = shutil.which(agent, path=str(folder))
            if found:
                return str(Path(found).resolve())
    return None


def _kill_tree(proc) -> None:
    with contextlib.suppress(Exception):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30,
                           check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.kill()


class CLIAgent(Provider):
    agent = ""

    def __init__(self, model: str = ""):
        self.model = model
        self.name = f"{self.agent}-cli"

    def command(self, binary: str) -> list[str]:
        raise NotImplementedError

    def unwrap(self, stdout: str) -> str:
        return ANSI.sub("", stdout).lstrip("﻿").strip()

    def _complete(self, prompt, system, json_mode, timeout, on_log):
        binary = find_binary(self.agent)
        if not binary:
            raise RuntimeError(INSTALL_HINTS[self.agent])
        full = f"{system}\n\n{prompt}" if system else prompt
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        with tempfile.TemporaryDirectory(prefix="caneta-") as empty:
            proc = subprocess.Popen(self.command(binary), cwd=empty, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **kwargs)
            out: list[str] = []
            err: list[str] = []

            def feed():
                with contextlib.suppress(OSError):
                    proc.stdin.write(full)
                    proc.stdin.close()

            def pump(stream, sink, log):
                for line in stream:
                    sink.append(line)
                    if log:
                        log(line.rstrip("\r\n"))

            threads = [threading.Thread(target=feed, daemon=True),
                       threading.Thread(target=pump, args=(proc.stdout, out, None), daemon=True),
                       threading.Thread(target=pump, args=(proc.stderr, err, on_log), daemon=True)]
            for t in threads:
                t.start()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                raise TimeoutError(f"{self.agent} não respondeu em {timeout}s") from None
            for t in threads:
                t.join(timeout=10)
        stdout, stderr = "".join(out), "".join(err)
        error = self.envelope_error(stdout)
        if proc.returncode != 0 or error:
            raise RuntimeError((error or stderr or stdout).strip()[-500:] or f"exit code {proc.returncode}")
        text = self.unwrap(stdout)
        if not text:
            raise RuntimeError("resposta vazia")
        return text

    def envelope_error(self, stdout: str) -> str | None:
        return None


class ClaudeCLI(CLIAgent):
    agent = "claude"

    def command(self, binary):
        cmd = [binary, "-p", "--output-format", "json"]
        return cmd + (["--model", self.model] if self.model else [])

    @staticmethod
    def _envelope(stdout: str) -> dict | None:
        try:
            data = json.loads(stdout)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def unwrap(self, stdout):
        env = self._envelope(stdout)
        if env is not None and isinstance(env.get("result"), str):
            return env["result"].strip()
        return super().unwrap(stdout)

    def envelope_error(self, stdout):
        env = self._envelope(stdout)
        if env is not None and env.get("is_error") is True:
            return str(env.get("result") or env.get("subtype") or "claude reported an error")
        return None


class CodexCLI(CLIAgent):
    agent = "codex"

    def command(self, binary):
        cmd = [binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only"]
        return cmd + (["--model", self.model] if self.model else []) + ["-"]
