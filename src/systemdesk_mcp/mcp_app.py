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
        "Use connect_composition_ports after creating or importing a composition to match open ports; "
        "after a successful create_composition call, pass its returned 'path' as "
        "connect_composition_ports' composition_path. Do not connect if creation failed. "
        "The strategy is optional and defaults to interface_type; "
        "port_name requires source_expression and target_expression; "
        "combined requires both and runs name matching first, then interface_strategy (default interface_type). "
        "Omit expressions for interface matching and omit interface_strategy unless using combined. "
        "Combined results include passes; on failure inspect completed passes and the model before retrying. "
        "It connects existing component and boundary ports without creating ports, "
        "and never connects ports without interfaces. "
        "Validate and save separately after connecting ports. "
        "All tools return a JSON dictionary with a 'status' field ('success' or 'error'). "
        "On error, 'error_type' is either 'permanent' (do not retry, halt or escalate) "
        "or 'transient' (retryable with backoff), and 'error_code' is a stable "
        "machine-parseable code identifying the failure condition (e.g. 'NO_PROJECT_OPEN'). "
        "After a 'TIMEOUT' error, the operation continues in SystemDesk; "
        "call get_systemdesk_status before retrying."
    ),
)
