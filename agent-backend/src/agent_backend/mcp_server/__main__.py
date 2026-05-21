"""Entry point: run the browser-control MCP server over stdio.

    python -m agent_backend.mcp_server

Reads CHROMIUM_CDP_URL from the environment (default http://127.0.0.1:9222).
"""

from __future__ import annotations

from .server import build_server


def main() -> None:  # pragma: no cover - launches the long-lived stdio server
    build_server().run()


if __name__ == "__main__":
    main()
