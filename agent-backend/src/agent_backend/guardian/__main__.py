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
from .metrics import GuardianMetrics, start_metrics_server
from .pin_store import PinStore
from .profiles import load_profiles
from .service import create_app


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    load_dotenv()
    try:
        config = GuardianConfig.from_env()
        registry = load_profiles(
            config.profiles_path,
            default_token=config.token,
            default_whitelist_path=config.whitelist_path,
            default_blocklist_path=config.blocklist_path,
            default_requests_path=config.requests_path,
            default_cache_path=config.cache_path,
            default_prompt_path=config.prompt_path,
        )
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    metrics = GuardianMetrics()
    app = create_app(config, registry=registry, metrics=metrics)
    start_metrics_server(metrics, config.metrics_port)
    profiles = ", ".join(p.name for p in registry.all())
    print(f"Aegis guardian listening on http://{config.host}:{config.port} (model: {config.model})")
    print(f"Teen profiles: {profiles}")
    print(f"Prometheus metrics on http://127.0.0.1:{config.metrics_port}/metrics")
    if not PinStore(config.admin_path, env_pin=config.parent_pin).is_configured():
        print(
            f"First-time setup: open http://{config.host}:{config.port}/setup "
            "to create your parent PIN."
        )
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
