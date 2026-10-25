"""`caneta` — start the local web app (127.0.0.1 only) and open the browser.

    caneta                 # free port, opens the browser
    caneta --port 8765     # fixed port
    caneta --no-browser    # print the URL instead
    caneta --enable-semantic   # download the meaning-search model and turn it on (installers use this)
"""

from __future__ import annotations

import argparse
import secrets
import socket
import threading
import time
import webbrowser

from . import __version__


def free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="caneta", description="CANETA — assistente aberto de escrita científica")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="version", version=f"caneta {__version__}")
    parser.add_argument("--enable-semantic", action="store_true",
                        help="baixa o modelo da busca por significado (≈0,5 GB), ativa e sai (usado pelos instaladores)")
    args = parser.parse_args(argv)

    if args.enable_semantic:
        from . import semantic

        semantic.enable(lambda line: print(line, flush=True))
        return

    import uvicorn

    from .app import create_app

    port = args.port or free_port()
    token = secrets.token_urlsafe(24)
    url = f"http://127.0.0.1:{port}/auth?t={token}"
    app = create_app(token=token)
    print(f"\n  CANETA {__version__} - {url}\n  (feche esta janela ou Ctrl+C para encerrar)\n", flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
