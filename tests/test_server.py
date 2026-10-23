"""Tests for server behavior."""
# pylint: disable=protected-access

import inspect
import json
import typing

import pytest
from packaging.version import Version
from pydantic.fields import FieldInfo
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import FunctionTool

import systemdesk_mcp.server as srv
from systemdesk_mcp import runtime


async def _registered_function_tools(mcp):
    """Return registered tools after checking they can be inspected as functions."""
    tools = await mcp.list_tools()
    assert tools
    for tool in tools:
        assert isinstance(tool, FunctionTool), (
            f"Tool '{tool.name}' is not a FunctionTool and cannot be inspected."
        )
    return tools


@pytest.mark.anyio
async def test_server_runs_tool_when_field_constraint_is_met(app):
    # arrange
    app.ActiveProject.Validate.ResultStructure.RuleResults = []

    # act
    result = await srv.mcp.call_tool("validate_autosar", {"timeout": 1})

    # assert
    assert json.loads(result.content[0].text)["status"] == "success"
    app.ActiveProject.Validate.Do.assert_called_once()


@pytest.mark.anyio
async def test_server_enforces_field_constraint_before_tool_runs(app):
    # act
    # Called on the server without a client, so the server must be what rejects it.
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("validate_autosar", {"timeout": 0})

    # assert
    assert not app.mock_calls, "Tool body must not run when arguments are invalid."


@pytest.mark.anyio
async def test_tools_are_logged_and_on_com_thread():
    # Every wrapper produced by a decorator shares that decorator's inner code object.
    logged_code = runtime.logged(lambda: None).__code__
    com_thread_code = runtime.on_com_thread(lambda: None).__code__

    tools = await _registered_function_tools(srv.mcp)

    for tool in tools:
        outer = tool.fn
        assert outer.__code__ is logged_code, (
            f"Tool '{tool.name}' must be decorated with @logged (outermost)."
        )
        inner = outer.__wrapped__
        assert inner.__code__ is com_thread_code, (
            f"Tool '{tool.name}' must be decorated with @on_com_thread below @logged."
        )
        assert not hasattr(inner.__wrapped__, "__wrapped__"), (
            f"Tool '{tool.name}' has unexpected extra decorators."
        )


@pytest.mark.anyio
async def test_tool_parameters_are_described_and_strict():
    tools = await _registered_function_tools(srv.mcp)

    for tool in tools:
        assert tool.parameters.get("additionalProperties") is False, (
            f"Tool '{tool.name}': schema must reject unknown arguments."
        )
        hints = typing.get_type_hints(tool.fn, include_extras=True)
        for name in inspect.signature(tool.fn).parameters:
            hint = hints.get(name)
            assert typing.get_origin(hint) is typing.Annotated and any(
                isinstance(m, FieldInfo) for m in hint.__metadata__
            ), (
                f"Tool '{tool.name}': parameter '{name}' must be Annotated[<type>, Field(...)]."
            )
        for name, prop in tool.parameters.get("properties", {}).items():
            assert "Example:" in prop.get("description", ""), (
                f"Tool '{tool.name}': parameter '{name}' needs a description with an example."
            )


@pytest.mark.anyio
async def test_tool_descriptions_list_failure_codes():
    tools = await _registered_function_tools(srv.mcp)

    for tool in tools:
        assert "Failure codes (error_type / error_code: condition):" in (tool.description or ""), (
            f"Tool '{tool.name}': description needs the standard failure-codes header."
        )


def test_version_is_pep440():
    # act
    version = Version(srv.__version__)

    # assert
    assert str(version) == srv.__version__, "Version must be in normalized PEP 440 form."
    assert len(version.release) == 3
