"""Compose the SystemDesk tools and start the MCP server over stdio."""

import fastmcp

from systemdesk_mcp import __version__
from systemdesk_mcp import tools as tools  # pylint: disable=unused-import,useless-import-alias
from systemdesk_mcp.mcp_app import mcp

__all__ = ["__version__", "mcp", "main"]


def main() -> None:
    """Start the MCP server with stdio transport."""
    # The banner otherwise queries PyPI for newer FastMCP releases on every start.
    fastmcp.settings.check_for_updates = "off"
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
