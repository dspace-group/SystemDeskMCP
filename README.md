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
2. Create the project environment and install dependencies from `pyproject.toml`:

```powershell
uv sync
```

## Using with an MCP Client

1. In your MCP client (e.g. VS Code, Claude Code, Cursor, Claude Desktop), add a new MCP server.
2. Configure it as a **stdio** MCP server using the following command:

   ```shell
   uv --directory path\to\this\cloned\repo run python -m systemdesk_mcp.server
   ```

   For example, as a `.vscode/mcp.json` entry:

      ```json
      {
       "servers": {
        "SystemDeskMCP": {
         "type": "stdio",
         "command": "uv",
         "args": [
          "--directory",
          "path\\to\\this\\cloned\\repo",
          "run",
          "python",
          "-m",
          "systemdesk_mcp.server"
         ]
        }
       }
      }
      ```

3. Reconnect/reload MCP servers in your client.
4. Run a quick check prompt, for example: "Call `start_systemdesk()` and report the result."

## Usage (Tool Order)

Recommended flow:

1. `start_systemdesk()`
2. `create_project(project_path, project_name)` or open an existing project
3. `import_autosar_file(file_path)`
4. `validate_autosar(output_format="markdown" | "json")`
5. `close_project(save_project=True)`
6. `close_systemdesk(save_project=True)`

Important:

- `start_systemdesk()` must be called before any other SystemDesk tool.
- `import_autosar_file` only supports files with the `.arxml` extension.
- If SystemDesk shows blocking dialogs, startup/COM access may fail transiently.

## Development Setup

Create the project environment and install the development dependencies from `pyproject.toml`:

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
- `close_systemdesk(save_project=True)`
- `create_project(project_path, project_name)`
- `close_project(save_project=True)`
- `import_autosar_file(file_path)`
- `validate_autosar(output_format="markdown" | "json")`

## Extending the Server

Add a new tool by defining a function decorated with `@mcp.tool()` in `src/systemdesk_mcp/server.py`:

```python
@mcp.tool()
def my_new_tool(param: str) -> str:
    """Description shown to the MCP client."""
    app = _get_app()  # retrieve the active COM connection
    # ... call app.* COM methods ...
    return "result"
```

Use `_get_app()` to access the live SystemDesk COM object.

## Troubleshooting

- **`win32com is not installed`**: run `uv sync` to install `pywin32`
- **`class not registered` / `invalid class string`**: check SystemDesk installation/COM registration
- **`blocked` / `rejected`**: close open dialogs in SystemDesk and try again
- **`access denied`**: start the MCP server with elevated privileges if needed

## File Overview

- `src/systemdesk_mcp/server.py`: MCP server and tools
- `tests/`: unit tests and ARXML test fixtures
- `pyproject.toml`: project metadata and Python dependencies (managed with `uv`)
- `.github/prompts/smoke-test.prompt.md`: end-to-end smoke test prompt
