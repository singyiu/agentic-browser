"""Unit tests for the Agent SDK runner (no real Claude/browser calls)."""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

import agent_backend.runner.agent as agent_mod
from agent_backend.config import RunnerConfig
from agent_backend.runner.agent import SERVER_NAME, allowed_tools, build_options, run_task


def _config() -> RunnerConfig:
    return RunnerConfig(oauth_token="tok-xyz", model="claude-test", cdp_url="http://127.0.0.1:9222")


def test_allowed_tools_are_namespaced() -> None:
    tools = allowed_tools()
    assert "mcp__browser__browser_navigate" in tools
    assert all(name.startswith("mcp__browser__") for name in tools)


def test_build_options_wires_isolation_and_tools() -> None:
    opts = build_options(_config(), "/tmp/cfg", python_executable="/usr/bin/python3")
    assert opts.model == "claude-test"
    assert opts.permission_mode == "bypassPermissions"
    assert "mcp__browser__browser_click" in opts.allowed_tools
    assert "Bash" in opts.disallowed_tools

    server = opts.mcp_servers[SERVER_NAME]
    assert server["command"] == "/usr/bin/python3"
    assert server["args"] == ["-m", "agent_backend.mcp_server"]
    assert server["env"]["CHROMIUM_CDP_URL"] == "http://127.0.0.1:9222"

    assert opts.env["CLAUDE_CONFIG_DIR"] == "/tmp/cfg"
    assert opts.env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-xyz"


def _assistant(*blocks: object) -> AssistantMessage:
    msg = object.__new__(AssistantMessage)
    msg.content = list(blocks)  # type: ignore[attr-defined]
    return msg


def _result(text: str, *, is_error: bool = False) -> ResultMessage:
    msg = object.__new__(ResultMessage)
    msg.result = text  # type: ignore[attr-defined]
    msg.is_error = is_error  # type: ignore[attr-defined]
    msg.total_cost_usd = 0.0123  # type: ignore[attr-defined]
    msg.num_turns = 3  # type: ignore[attr-defined]
    msg.errors = None  # type: ignore[attr-defined]
    return msg


async def test_run_task_collects_events_and_final_result(monkeypatch) -> None:
    async def fake_query(*, prompt: str, options: object, transport: object = None):
        yield _assistant(TextBlock(text="thinking..."))
        yield _result("done: H1 is Demo Heading")

    monkeypatch.setattr(agent_mod, "query", fake_query)

    events: list[str] = []
    result = await run_task("task", _config(), "/tmp/cfg", on_event=events.append)

    assert result == "done: H1 is Demo Heading"
    assert any("thinking" in e for e in events)
    assert any("cost" in e for e in events)


async def test_run_task_emits_error_event(monkeypatch) -> None:
    async def fake_query(*, prompt: str, options: object, transport: object = None):
        yield _result("it failed", is_error=True)

    monkeypatch.setattr(agent_mod, "query", fake_query)

    events: list[str] = []
    await run_task("task", _config(), "/tmp/cfg", on_event=events.append)
    assert any("[error]" in e for e in events)


async def test_run_task_emits_tool_use_event(monkeypatch) -> None:
    tool = object.__new__(ToolUseBlock)
    tool.name = "mcp__browser__browser_click"  # type: ignore[attr-defined]
    tool.input = {"selector": "a"}  # type: ignore[attr-defined]

    async def fake_query(*, prompt: str, options: object, transport: object = None):
        yield _assistant(tool)
        yield _result("done")

    monkeypatch.setattr(agent_mod, "query", fake_query)

    events: list[str] = []
    await run_task("task", _config(), "/tmp/cfg", on_event=events.append)
    assert any("[tool]" in e and "browser_click" in e for e in events)
