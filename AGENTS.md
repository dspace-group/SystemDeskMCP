# AGENTS.md

Python MCP server (FastMCP, stdio) that automates dSPACE SystemDesk via its COM interface (`pywin32`). Windows only. See [README.md](README.md) for user-facing setup and usage.

## Layout

- [src/systemdesk_mcp/server.py](src/systemdesk_mcp/server.py): imports the tools package and starts the stdio server.
- [src/systemdesk_mcp/mcp_app.py](src/systemdesk_mcp/mcp_app.py): shared FastMCP instance and instructions.
- [src/systemdesk_mcp/runtime.py](src/systemdesk_mcp/runtime.py): sole owner of COM state, worker thread, timeouts, and cleanup; also provides tool invocation logging.
- [src/systemdesk_mcp/common.py](src/systemdesk_mcp/common.py): shared errors, responses, and input constraints.
- [src/systemdesk_mcp/tools/](src/systemdesk_mcp/tools/): lifecycle, project, and AUTOSAR tool implementations. Domain helpers stay alongside their tools; tool modules never import `server.py`.
- [tests/](tests/): tool behavior and constraint tests and server integration contracts; COM is mocked, no SystemDesk needed.
- [tests/tools/](tests/tools/): package for lifecycle, project, and AUTOSAR tool behavior tests; inherits the parent COM fixture.
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

Tool modules are imported explicitly in `tools/__init__.py`. Add new domain modules to its imports and `__all__`; no server imports or re-exports are needed. Adding tools to an already imported module requires no registration changes. Shared helpers and constraints have public names; module implementation details remain underscore-prefixed.

## Testing conventions

- Arrange / act / assert structure with comments.
- Use the autouse `app` fixture (a `MagicMock` installed as `runtime._application`); set `runtime._application = None` to simulate "not started".
- Import tools from their owning modules and call them directly with keyword arguments, e.g. `autosar.validate_autosar(output_format=autosar.ValidateOutputFormat.JSON)`. Direct calls skip Pydantic validation.
- Assert on `status`, `error_type`, and `error_code`, not just messages.
- Add schema tests for new input constraints to the owning domain's test module under `tests/tools/`, using `pytest.raises(ValidationError)` around `validate_args(<module>.<tool>, ...)`, imported from `tests.helpers`. Shared valid and invalid absolute-path examples also live in `tests.helpers`.
- Keep tool behavior and constraint tests together under `tests/tools/`. Keep all-tools schema contracts, cross-module workflows, and MCP calls in `tests/test_server.py`. Never import helpers from another test module or directly from `conftest.py`.
- Use `tmp_path` for any files; do not write outside it.

## Gotchas

- SystemDesk is started with `BatchMode = True` to suppress dialogs; modal dialogs still cause transient `BLOCKED_BY_DIALOG` failures.
- On timeout the COM call keeps running on the single worker thread; subsequent calls queue behind it.
- Bump `__version__` in [src/systemdesk_mcp/__init__.py](src/systemdesk_mcp/__init__.py) and `version` in [pyproject.toml](pyproject.toml) together. The server re-exports the package version.
