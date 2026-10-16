"""Compose the SystemDesk tools and start the MCP server over stdio."""

from systemdesk_mcp import __version__
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.tools.lifecycle import (
    start_systemdesk,
    close_systemdesk,
    get_systemdesk_status,
)
from systemdesk_mcp.tools.projects import create_project, close_project
from systemdesk_mcp.tools.autosar import (
    import_autosar_file,
    validate_autosar,
    ValidateOutputFormat,
)

__all__ = [
    "__version__",
    "mcp",
    "start_systemdesk",
    "close_systemdesk",
    "get_systemdesk_status",
    "create_project",
    "close_project",
    "import_autosar_file",
    "validate_autosar",
    "ValidateOutputFormat",
    "main",
]


def main() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
