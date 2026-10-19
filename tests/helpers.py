"""Shared schema validation and tool inspection helpers for tests."""

import inspect
import typing

from pydantic import TypeAdapter
from fastmcp.tools import FunctionTool


def validate_args(tool, **kwargs):
    """Validate tool arguments without invoking the tool body."""
    def stub(*_args, **_kwargs):
        return None
    stub.__signature__ = inspect.signature(tool)
    stub.__annotations__ = typing.get_type_hints(tool, include_extras=True)
    TypeAdapter(stub).validate_python(kwargs)


async def registered_function_tools(mcp):
    """Return registered tools after checking they can be inspected as functions."""
    tools = await mcp.list_tools()
    assert tools
    for tool in tools:
        assert isinstance(tool, FunctionTool), (
            f"Tool '{tool.name}' is not a FunctionTool and cannot be inspected."
        )
    return tools


VALID_ABSOLUTE_DIRS = [
    "C:\\",
    "C:/",
    "c:\\Projects",
    "C:\\Projects\\My ECU",
    "C:/Projects/MyECU",
    "C:\\Projects/Mixed\\Separators",
    "\\\\server\\share",
    "\\\\server\\share\\Projects",
    "//server/share/Projects",
    "\\\\192.168.0.1\\share\\Projects",
    "\\\\?\\C:\\Very\\Long\\Path",
]

INVALID_ABSOLUTE_DIRS = [
    "Projects",
    "Projects\\MyECU",
    ".\\Projects",
    "..\\Projects",
    "\\Projects",
    "/Projects",
    "C:",
    "C:Projects",
    "1:\\Projects",
    "\\\\server",
    "\\\\server\\",
    "\\\\\\share",
]
