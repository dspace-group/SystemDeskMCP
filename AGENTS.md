# AGENTS.md

Python MCP server (FastMCP, stdio) that automates dSPACE SystemDesk via its COM interface (`pywin32`). Windows only. See [README.md](README.md) for user-facing setup and usage.

## Layout

- [src/systemdesk_mcp/server.py](src/systemdesk_mcp/server.py): server and all tools.
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
- Use the autouse `app` fixture (a `MagicMock` installed as `srv._application`); set `srv._application = None` to simulate "not started".
- Call tools directly with their input model, e.g. `srv.validate_autosar(srv.ValidateAutosarInput())`.
- Assert on `status`, `error_type`, and `error_code`, not just messages.
- Add schema tests (`pytest.raises(ValidationError)`) for new input constraints.
- Use `tmp_path` for any files; do not write outside it.

## Gotchas

- SystemDesk is started with `BatchMode = True` to suppress dialogs; modal dialogs still cause transient `BLOCKED_BY_DIALOG` failures.
- On timeout the COM call keeps running on the single worker thread; subsequent calls queue behind it.
- Bump `__version__` in `server.py` and `version` in [pyproject.toml](pyproject.toml) together.
