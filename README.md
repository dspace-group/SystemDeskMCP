# dSPACE SystemDesk MCP Server

This MCP server automates dSPACE SystemDesk through its COM automation interface.
It covers the SystemDesk lifecycle, project operations, and AUTOSAR file import/validation.

SystemDesk acts as a strong partner for AI-driven AUTOSAR generation, where AI agents create comprehensive architecture descriptions and SystemDesk ensures correctness, consistency, and compliance through its robust validation capabilities.

## Prerequisites

- Installed dSPACE SystemDesk with a valid license
- Python 3.10+ (recommended)
- An MCP client (e.g. VS Code, Cursor, Claude Desktop)

## Installation

1. Open the repository/folder.
2. To use the startup script, run `SystemDeskMCP.cmd`. The script creates a `.venv`, installs dependencies from `requirements.txt`, and starts `src\systemdesk_mcp_server.py`.
3. For manual setup, create and activate a virtual environment, then install dependencies:

```powershell
pip install -r requirements.txt
```

## Using with an MCP Client

1. In your MCP client (e.g. VS Code, Claude Code, Cursor, Claude Desktop), add a new MCP server.
2. Configure it as a **stdio** MCP server:
    - Startup script: use command `SystemDeskMCP.cmd`.
    - Manual: use command `python` with argument `src\systemdesk_mcp_server.py` inside an already-prepared environment.
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

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

### Running Tests

```powershell
python -m pytest
```

To also collect coverage:

```powershell
python -m pytest --cov=systemdesk_mcp_server
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

Add a new tool by defining a function decorated with `@mcp.tool()` in `systemdesk_mcp_server.py`:

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

- **`win32com is not installed`**: `pip install pywin32`
- **`class not registered` / `invalid class string`**: check SystemDesk installation/COM registration
- **`blocked` / `rejected`**: close open dialogs in SystemDesk and try again
- **`access denied`**: start the MCP server with elevated privileges if needed

## File Overview

- `src/systemdesk_mcp_server.py`: MCP server and tools
- `tests/`: unit tests and ARXML test fixtures
- `requirements.txt`: Python dependencies for running the MCP server
- `requirements-dev.txt`: Additional Python dependencies for development
- `SystemDeskMCP.cmd`: convenience launcher that sets up a `.venv`, installs dependencies, and starts the server
- `.github/prompts/smoke-test.prompt.md`: end-to-end smoke test prompt
