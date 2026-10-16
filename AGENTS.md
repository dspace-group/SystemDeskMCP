# AGENTS.md

Python MCP server (FastMCP, stdio) that automates dSPACE SystemDesk via its COM interface (`pywin32`). Windows only. See [README.md](README.md) for user-facing setup and usage.

## Layout

- [src/systemdesk_mcp/server.py](src/systemdesk_mcp/server.py): tool registration, compatibility exports, and stdio entry point.
- [src/systemdesk_mcp/mcp_app.py](src/systemdesk_mcp/mcp_app.py): shared FastMCP instance and instructions.
- [src/systemdesk_mcp/runtime.py](src/systemdesk_mcp/runtime.py): sole owner of COM state, worker thread, timeouts, and cleanup.
- [src/systemdesk_mcp/common.py](src/systemdesk_mcp/common.py): shared errors, responses, logging, and input constraints.
- [src/systemdesk_mcp/tools/](src/systemdesk_mcp/tools/): lifecycle, project, and AUTOSAR tool implementations. Domain helpers stay alongside their tools; tool modules never import `server.py`.
- [tests/](tests/): unit tests; COM is mocked, no SystemDesk needed.
- [.github/prompts/smoke-test.prompt.md](.github/prompts/smoke-test.prompt.md): end-to-end smoke test against a real SystemDesk installation (requires a license).

## Commands

Use `uv` for everything; do not use `pip` directly.

```powershell
uv sync --extra dev        # install runtime + dev dependencies
uv run pylint src tests    # lint (must pass, CI runs this)
uv run pytest              # unit tests (must pass, CI runs this)
uv run pytest --cov=systemdesk_mcp
```

## Tool conventions

When adding or changing a tool, follow [Extending the Server](README.md#extending-the-server) in the README.

## Testing conventions

- Arrange / act / assert structure with comments.
- Use the autouse `app` fixture (a `MagicMock` installed as `runtime._application`); set `runtime._application = None` to simulate "not started".
- Call tools directly with keyword arguments, e.g. `srv.validate_autosar(output_format=srv.ValidateOutputFormat.JSON)`. Direct calls skip Pydantic validation.
- Assert on `status`, `error_type`, and `error_code`, not just messages.
- Add schema tests for new input constraints with `pytest.raises(ValidationError)` around `_validate_args(srv.<tool>, ...)`.
- Use `tmp_path` for any files; do not write outside it.

## Gotchas

- SystemDesk is started with `BatchMode = True` to suppress dialogs; modal dialogs still cause transient `BLOCKED_BY_DIALOG` failures.
- On timeout the COM call keeps running on the single worker thread; subsequent calls queue behind it.
- Bump `__version__` in [src/systemdesk_mcp/__init__.py](src/systemdesk_mcp/__init__.py) and `version` in [pyproject.toml](pyproject.toml) together. The server re-exports the package version.
