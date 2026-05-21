"""Entry point: run the guardian HTTP service.

    python -m agent_backend.guardian

Reads GUARDIAN_* + CLAUDE_CODE_OAUTH_TOKEN + CLAUDE_CONFIG_DIR from the environment
(scripts/launch-guardian.sh sets them and loads .env).
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from ..config import ConfigError
from .config import GuardianConfig
from .service import create_app


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    load_dotenv()
    try:
        config = GuardianConfig.from_env()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    app = create_app(config)
    print(f"Guardian listening on http://{config.host}:{config.port} (model: {config.model})")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
