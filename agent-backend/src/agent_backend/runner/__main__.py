"""Entry point: run an autonomous browser task.

    python -m agent_backend.runner "Open example.com and report the H1"

Expects CLAUDE_CONFIG_DIR to be set (scripts/run-agent.sh does this) and
CLAUDE_CODE_OAUTH_TOKEN available (loaded from agent-backend/.env).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

from ..config import ConfigError, RunnerConfig
from .agent import run_task


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="Run an autonomous browser task with Claude Max (browser-control MCP).",
    )
    parser.add_argument("task", help="Natural-language task for the agent")
    args = parser.parse_args()

    load_dotenv()

    config_dir = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not config_dir:
        print(
            "CLAUDE_CONFIG_DIR is not set. "
            "Run via scripts/run-agent.sh (it sets the isolated config dir).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        config = RunnerConfig.from_env()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    result = asyncio.run(run_task(args.task, config, config_dir))
    if result:
        print("\n=== result ===")
        print(result)


if __name__ == "__main__":
    main()
