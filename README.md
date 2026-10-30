# dSPACE SystemDesk MCP Server

This MCP server automates dSPACE SystemDesk through its COM automation interface.
It covers the SystemDesk lifecycle, project operations, AUTOSAR file import/validation,
and composition creation from existing software component types.

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
2. `create_project(project_path, project_name)` or `open_project(project_file, timeout=60)`
3. `import_autosar_file(file_path, timeout=60)`
4. `create_composition(composition_path, component_paths)` (optional)
5. `validate_autosar(output_format="markdown" | "json", timeout=120)`
6. `get_or_create_vecu(vecu_name=None, reuse_existing_vecu=False, component_path=None, system_name=None, predefined_ecu_configuration="Default ECU Configuration for V-ECU FMUs (FMI 3.0)", auto_configure_and_generate=False, timeout=120)` (further optional V-ECU creation options are available)
7. `generate_vecu_bsw(vecu_name, timeout=120)` (pass the `vecu_name` returned by `get_or_create_vecu`)
8. `export_vecu_fmu(vecu_name, target_directory=None, timeout=300)`
9. `close_project(save_project=True)` (optional, only to close the project)
10. `close_systemdesk(save_project=True)` (optional, only to quit SystemDesk)

Important:

- `start_systemdesk()` must be called before tools whose precondition requires it.
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

For an end-to-end smoke test against an existing SystemDesk installation, use the
[smoke-test skill](.github/skills/smoke-test/SKILL.md).

## Available MCP Tools

- `start_systemdesk()`
- `get_systemdesk_status()`
- `close_systemdesk(save_project=True)`
- `create_project(project_path, project_name)`
- `open_project(project_file, timeout=60)`
- `close_project(save_project=True)`
- `import_autosar_file(file_path, timeout=60)`
- `create_composition(composition_path, component_paths)`
- `validate_autosar(output_format="markdown" | "json", timeout=120)`
- `get_or_create_vecu(vecu_name=None, reuse_existing_vecu=False, component_path=None, system_name=None, predefined_ecu_configuration="Default ECU Configuration for V-ECU FMUs (FMI 3.0)", generate_dap_for_atomic_ports=None, generate_dap_for_root_composition_ports=None, create_runnable_mapping=None, rte_calibration_support=None, rte_simulation_logging=None, rte_measurement_support=None, auto_configure_and_generate=False, timeout=120)`
- `generate_vecu_bsw(vecu_name, timeout=120)`
- `export_vecu_fmu(vecu_name, target_directory=None, timeout=300)`

## Extending the Server

Add a new tool in the appropriate domain-specific module under `src/systemdesk_mcp/tools/`. For a new domain, add a module under `tools/`, then add it to the imports and `__all__` in `tools/__init__.py`.

Use the shared `mcp` instance from `mcp_app.py`, COM access, execution, and logging helpers from `runtime.py`, and errors, responses, and input constraints from `common.py`. Keep domain-specific helpers alongside their tools.

Declare each input as a flat function parameter typed `Annotated[<type>, Field(...)]`: the type, constraints, and description (with an `Example:`) live in the `Field`, the default in the signature. This is the single definition; FastMCP derives the JSON schema (including `additionalProperties: false`) and validates calls from it. Constraints shared across tools are defined once as `Annotated` aliases (e.g. `TimeoutSeconds`) and combined with a tool-specific description. Tools without inputs take no parameters.

```python
from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode, PermanentError, TimeoutSeconds, error_response, success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_app, logged, on_com_thread


@mcp.tool(
    title="My New Tool",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None,
)
@logged
@on_com_thread
def my_new_tool(
    element_path: Annotated[str, Field(
        min_length=1,
        description="AUTOSAR path of the element. Example: '/Package/MyElement'",
    )],
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """Description shown to the MCP client."""
    try:
        app = get_app()  # active COM connection
        # ... call app.* COM methods using element_path ...
        return success_response({"message": "Done."})
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "my_new_tool failed.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )
```

Notes:

- Keep the decorators exactly in the order shown, with no others (checked by tests). Set all four annotations.
- Use `get_app()` to access the live SystemDesk COM object. Never touch COM objects outside the dedicated COM thread.
- `@on_com_thread` runs the tool on the dedicated COM thread. If the operation can block, add a `timeout` parameter as shown; it is used as the call timeout.
- `@logged` writes a structured log entry for every call.
- Validate file paths with the existing `*_PATTERN` regexes; only absolute local or UNC paths are accepted.
- Return `success_response(...)` or `error_response(...)` so that every tool has the same `status` / `error_type` / `error_code` response format. Use `error_type="permanent"` (via `PermanentError`) for non-retryable failures and for the generic `except Exception` fallback; reserve `"transient"` for retryable conditions such as timeouts or blocking dialogs.
- Use `ErrorCode.UNEXPECTED_ERROR` for the generic `except Exception` fallback. Add a stable UPPER_SNAKE_CASE `error_code` only for a distinct failure condition the client can act on: add it as a member of the `ErrorCode` enum (e.g. `FILE_NOT_FOUND = "FILE_NOT_FOUND"`), pass `ErrorCode.<MEMBER>` to `error_response(...)` or `PermanentError(message, error_code=...)`, and list it in the tool's "Failure codes" docstring section. Never rename or remove existing codes; clients depend on them.
- Document in the docstring: purpose, preconditions, return fields, and a `Failure codes (error_type / error_code: condition):` section listing every code the tool can return.
- Update [Usage (Tool Order)](#usage-tool-order), [Available MCP Tools](#available-mcp-tools), and, if relevant, the `instructions` string passed to `FastMCP(...)`.

## Troubleshooting

- **`win32com is not installed`**: run `uv sync` to install `pywin32`
- **`class not registered` / `invalid class string`**: check SystemDesk installation/COM registration
- **`blocked` / `rejected`**: close open dialogs in SystemDesk and try again
- **`access denied`**: start the MCP server with elevated privileges if needed

## File Overview

- `src/systemdesk_mcp/server.py`: imports the tools package and starts the stdio server
- `src/systemdesk_mcp/tools/`: domain-specific tool implementations
- `src/systemdesk_mcp/tools/__init__.py`: imports for domain-specific module registration
- `src/systemdesk_mcp/mcp_app.py`: shared FastMCP instance and server instructions
- `src/systemdesk_mcp/runtime.py`: shared COM connection, worker thread, timeouts, cleanup, and tool logging
- `src/systemdesk_mcp/common.py`: shared errors, responses, and input constraints
- `src/systemdesk_mcp/__init__.py`: package version
- `tests/`: unit tests and ARXML test fixtures
- `pyproject.toml`: project metadata and Python dependencies (managed with `uv`)
- `SystemDeskMCP.cmd`: Convenience launcher used by MCP clients
- `.github/skills/smoke-test/SKILL.md`: end-to-end smoke test skill

## Support

For technical questions and issues related to the dSPACE MCP Servers and related GitHub repositories, please open a GitHub issue.  
As a valued dSPACE customer, you are always welcome to contact dSPACE Support directly via http://www.dspace.com/go/supportrequest.
