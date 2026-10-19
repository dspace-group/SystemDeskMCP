"""Compose the SystemDesk tools and start the MCP server over stdio."""

from systemdesk_mcp import __version__
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.tools import load_tools

__all__ = ["__version__", "mcp", "main"]


load_tools()


def main() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
