# AGENTS.md

Python MCP server (FastMCP, stdio) that automates dSPACE SystemDesk via its COM interface (`pywin32`). Windows only. See [README.md](README.md) for user-facing setup and usage.

## Layout

- [src/systemdesk_mcp/server.py](src/systemdesk_mcp/server.py): automatic tool discovery and stdio entry point.
- [src/systemdesk_mcp/mcp_app.py](src/systemdesk_mcp/mcp_app.py): shared FastMCP instance and instructions.
- [src/systemdesk_mcp/runtime.py](src/systemdesk_mcp/runtime.py): sole owner of COM state, worker thread, timeouts, and cleanup.
- [src/systemdesk_mcp/common.py](src/systemdesk_mcp/common.py): shared errors, responses, logging, and input constraints.
- [src/systemdesk_mcp/tools/](src/systemdesk_mcp/tools/): lifecycle, project, and AUTOSAR tool implementations. Domain helpers stay alongside their tools; tool modules never import `server.py`.
- [tests/](tests/): tests grouped by lifecycle, projects, AUTOSAR, schemas, discovery, and server integration; COM is mocked, no SystemDesk needed.
- [tests/conftest.py](tests/conftest.py): shared autouse COM application fixture with automatic state restoration.
- [tests/helpers.py](tests/helpers.py): shared argument validation and registered-tool inspection helpers.
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

Public modules directly under `tools/` are imported automatically at startup. No server imports or re-exports are needed for new tools. Prefix helper-only modules with `_`. Shared helpers and constraints have public names; module implementation details remain underscore-prefixed.

## Testing conventions

- Arrange / act / assert structure with comments.
- Use the autouse `app` fixture (a `MagicMock` installed as `runtime._application`); set `runtime._application = None` to simulate "not started".
- Import tools from their owning modules and call them directly with keyword arguments, e.g. `autosar.validate_autosar(output_format=autosar.ValidateOutputFormat.JSON)`. Direct calls skip Pydantic validation.
- Assert on `status`, `error_type`, and `error_code`, not just messages.
- Add schema tests in `tests/test_schemas.py` for new input constraints with `pytest.raises(ValidationError)` around `validate_args(<module>.<tool>, ...)`, imported from `tests.helpers`.
- Add behavior tests to the owning domain's test module. Keep cross-module workflows and MCP calls in `tests/test_server.py`, and tool-loading checks in `tests/test_discovery.py`. Never import helpers from another test module or directly from `conftest.py`.
- Use `tmp_path` for any files; do not write outside it.

## Gotchas

- SystemDesk is started with `BatchMode = True` to suppress dialogs; modal dialogs still cause transient `BLOCKED_BY_DIALOG` failures.
- On timeout the COM call keeps running on the single worker thread; subsequent calls queue behind it.
- Bump `__version__` in [src/systemdesk_mcp/__init__.py](src/systemdesk_mcp/__init__.py) and `version` in [pyproject.toml](pyproject.toml) together. The server re-exports the package version.
