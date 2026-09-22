"""Packaged GUI entry point: start TokenFlow and open the local console."""

from __future__ import annotations

import os
import threading
import webbrowser

import tokenflow


def main() -> None:
    host = os.environ.get("TOKENFLOW_HOST", "127.0.0.1")
    port = int(os.environ.get("TOKENFLOW_PORT", "8765"))
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    tokenflow.run_server(host, port)


if __name__ == "__main__":
    main()
