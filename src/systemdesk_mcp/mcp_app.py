"""Shared MCP application; tool registration is composed by the entry point."""

from fastmcp import FastMCP

mcp = FastMCP(
    "SystemDesk MCP Server",
    instructions=(
        "Use start_systemdesk before calling any other tool. "
        "Use close_systemdesk to release the COM connection when done. "
        "AUTOSAR files must use the .arxml extension. "
        "All tools return a JSON dictionary with a 'status' field ('success' or 'error'). "
        "On error, 'error_type' is either 'permanent' (do not retry, halt or escalate) "
        "or 'transient' (retryable with backoff), and 'error_code' is a stable "
        "machine-parseable code identifying the failure condition (e.g. 'NO_PROJECT_OPEN'). "
        "After a 'TIMEOUT' error, the operation continues in SystemDesk; "
        "call get_systemdesk_status before retrying."
    ),
)
