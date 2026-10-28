"""Shared MCP application; tool registration is composed by the entry point."""

from fastmcp import FastMCP

from systemdesk_mcp import __version__

mcp = FastMCP(
    "SystemDesk MCP Server",
    version=__version__,
    instructions=(
        "Call start_systemdesk when a tool's precondition requires it. "
        "Call close_systemdesk only when SystemDesk should quit; "
        "the COM connection may stay open otherwise. "
        "AUTOSAR files must use the .arxml extension. "
        "All tools return a JSON dictionary with a 'status' field ('success' or 'error'). "
        "On error, 'error_type' is either 'permanent' (do not retry, halt or escalate) "
        "or 'transient' (retryable with backoff), and 'error_code' is a stable "
        "machine-parseable code identifying the failure condition (e.g. 'NO_PROJECT_OPEN'). "
        "After a 'TIMEOUT' error, the operation continues in SystemDesk; "
        "call get_systemdesk_status before retrying."
    ),
)
