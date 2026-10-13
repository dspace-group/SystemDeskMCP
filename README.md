# dSPACE SystemDesk MCP Server

This MCP server automates dSPACE SystemDesk through its COM automation interface.
It covers the SystemDesk lifecycle, project operations, and AUTOSAR file import/validation.

SystemDesk acts as a strong partner for AI-driven AUTOSAR generation, where AI agents create comprehensive architecture descriptions and SystemDesk ensures correctness, consistency, and compliance through its robust validation capabilities.

## Prerequisites

- Installed dSPACE SystemDesk with a valid license
- Python 3.10 or newer
- `uv`, the Python package and project manager used to create the environment, install dependencies, and run the server from this checkout. Install it from the official Astral documentation: <https://docs.astral.sh/uv/getting-started/installation/>
- An MCP client (e.g. VS Code, Cursor, Claude Desktop)

## Installation

1. Open the repository/folder.
2. Create the project environment and install dependencies from `pyproject.toml` using the following command:

```powershell
uv sync
```

## Using with an MCP Client

1. In your MCP client (e.g. VS Code, Claude Code, Cursor, Claude Desktop), add a new MCP server.
2. Configure it as a **stdio** MCP server using the following command:

   ```powershell
    path\to\this\cloned\repo\SystemDeskMCP.cmd
   ```

   For example, as a `.vscode/mcp.json` entry:

      ```json
      {
       "servers": {
        "SystemDeskMCP": {
         "type": "stdio",
         "command": "path\\to\\this\\cloned\\repo\\SystemDeskMCP.cmd",
         "args": []
        }
       }
      }
      ```

3. Reconnect/reload MCP servers in your client.
4. Run a quick check prompt, for example: "Call `start_systemdesk()` and report the result."

## Usage (Tool Order)

Recommended flow:

1. `start_systemdesk()`
2. `create_project(params={project_path, project_name})` or open an existing project
3. `import_autosar_file(params={file_path, timeout=60})`
4. `validate_autosar(params={output_format="markdown" | "json", timeout=120})`
5. `close_project(params={save_project=True})`
6. `close_systemdesk(params={save_project=True})`

Important:

- `start_systemdesk()` must be called before any other SystemDesk tool.
- `import_autosar_file` only supports files with the `.arxml` extension.
- If SystemDesk shows blocking dialogs, startup/COM access may fail transiently.

## Development Setup

Create the project environment and install the development dependencies from `pyproject.toml` using the following command:

```powershell
uv sync --extra dev
```

### Running Tests

```powershell
uv run pytest
```

To also collect coverage:

```powershell
uv run pytest --cov=systemdesk_mcp
```

For an end-to-end smoke test against an existing SystemDesk installation, use the prompt in `.github/prompts/smoke-test.prompt.md`.

## Available MCP Tools

- `start_systemdesk()`
- `get_systemdesk_status()`
- `close_systemdesk(params={save_project=True})`
- `create_project(params={project_path, project_name})`
- `close_project(params={save_project=True})`
- `import_autosar_file(params={file_path, timeout=60})`
- `validate_autosar(params={output_format="markdown" | "json", timeout=120})`

## Extending the Server

Add a new tool in `src/systemdesk_mcp/server.py`. If the tool has input parameters, define a Pydantic model for them that inherits from `_StrictInput` and use it as the tool's only parameter, named `params`. Field types, defaults, constraints, and descriptions (with an `Example:`) are defined only in the model. Tools without inputs take no parameters.

```python
class MyNewToolInput(_StrictInput):
    """Input schema for my_new_tool."""

    element_path: str = Field(
        description="AUTOSAR path of the element. Example: '/Package/MyElement'",
    )
    timeout: int = Field(
        default=60,
        ge=1,
        le=_MAX_TIMEOUT_SECONDS,
        description="Maximum number of seconds to wait. Example: 60",
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None,
)
@_logged
@_on_com_thread
def my_new_tool(params: MyNewToolInput) -> dict[str, Any]:
    """Description shown to the MCP client."""
    try:
        app = _get_app()  # active COM connection
        # ... call app.* COM methods using params.element_path ...
        return _ok({"message": "Done."})
    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error(
            "my_new_tool failed.",
            error_type="transient",
            error_code=ErrorCode.MY_NEW_TOOL_FAILED,
            details=str(exc),
        )
```

Notes:

- Keep the decorators exactly in the order shown, with no others (checked by tests). Set all four annotations.
- Use `_get_app()` to access the live SystemDesk COM object. Never touch COM objects outside the dedicated COM thread.
- `@_on_com_thread` runs the tool on the dedicated COM thread. If the operation can block, add a `timeout` field as shown; it is used as the call timeout.
- `@_logged` writes a structured log entry for every call.
- Validate file paths with the existing `_*_PATTERN` regexes; only absolute local or UNC paths are accepted.
- Return `_ok(...)` or `_error(...)` so that every tool has the same `status` / `error_type` / `error_code` response format. Use `error_type="permanent"` (via `_PermanentError`) for non-retryable failures and `"transient"` for the generic `except Exception` fallback.
- Use a stable UPPER_SNAKE_CASE `error_code` per failure condition: add it as a member of the `ErrorCode` enum (e.g. `MY_NEW_TOOL_FAILED = "MY_NEW_TOOL_FAILED"`), pass `ErrorCode.<MEMBER>` to `_error(...)` or `_PermanentError(message, error_code=...)`, and list it in the tool's "Failure codes" docstring section. Never rename or remove existing codes; clients depend on them.
- Document in the docstring: purpose, preconditions, return fields, and a `Failure codes (error_type / error_code: condition):` section listing every code the tool can return.
- Update [Usage (Tool Order)](#usage-tool-order), [Available MCP Tools](#available-mcp-tools), and, if relevant, the `instructions` string passed to `FastMCP(...)`.

## Troubleshooting

- **`win32com is not installed`**: run `uv sync` to install `pywin32`
- **`class not registered` / `invalid class string`**: check SystemDesk installation/COM registration
- **`blocked` / `rejected`**: close open dialogs in SystemDesk and try again
- **`access denied`**: start the MCP server with elevated privileges if needed

## File Overview

- `src/systemdesk_mcp/server.py`: MCP server and tools
- `tests/`: unit tests and ARXML test fixtures
- `pyproject.toml`: project metadata and Python dependencies (managed with `uv`)
- `SystemDeskMCP.cmd`: Convenience launcher used by MCP clients
- `.github/prompts/smoke-test.prompt.md`: end-to-end smoke test prompt

## Support

For technical questions and issues related to the dSPACE MCP Servers and related GitHub repositories, please open a GitHub issue.  
As a valued dSPACE customer, you are always welcome to contact dSPACE Support directly via http://www.dspace.com/go/supportrequest.
