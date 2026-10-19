"""Compose the SystemDesk tools and start the MCP server over stdio."""

from systemdesk_mcp import __version__, tools  # pylint: disable=unused-import
from systemdesk_mcp.mcp_app import mcp

__all__ = ["__version__", "mcp", "main"]


def main() -> None:
    """Start the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
