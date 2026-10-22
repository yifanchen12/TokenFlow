"""Packaged GUI entry point: start TokenFlow and open the local console."""

from __future__ import annotations

import os

import tokenflow


def main() -> None:
    host = os.environ.get("TOKENFLOW_HOST", "127.0.0.1")
    port = int(os.environ.get("TOKENFLOW_PORT", "8765"))
    tokenflow.run_server(host, port, open_browser=os.environ.get("TOKENFLOW_OPEN_BROWSER", "1") != "0", reuse_existing=True)


if __name__ == "__main__":
    main()
