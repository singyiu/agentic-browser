"""Unit tests for the runner CLI entry point (agent_backend.runner.__main__)."""

from __future__ import annotations

import sys

import pytest

import agent_backend.runner.__main__ as cli


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never load a real .env during tests.
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)


def test_missing_config_dir_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent-backend", "do a thing"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_config_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/cfg")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent-backend", "do a thing"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_happy_path_prints_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/cfg")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent-backend", "do a thing"])

    async def fake_run_task(task: str, config: object, config_dir: str, **kwargs: object) -> str:
        return "RESULT-OK"

    monkeypatch.setattr(cli, "run_task", fake_run_task)
    cli.main()
    assert "RESULT-OK" in capsys.readouterr().out
