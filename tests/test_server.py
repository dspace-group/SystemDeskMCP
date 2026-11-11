"""Tests for server behavior."""
# pylint: disable=protected-access

import inspect
import json
import typing
from unittest.mock import MagicMock

import pytest
from packaging.version import Version
from pydantic.fields import FieldInfo
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import FunctionTool

import systemdesk_mcp.server as srv
from systemdesk_mcp import runtime


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", [False, True])
@pytest.mark.parametrize("matching_arguments", [{}, {"strategy": "interface_type"}])
async def test_connect_composition_ports_mcp_workflow(app, boundary, matching_arguments):
    # arrange
    component = MagicMock(AutosarType="APPLICATION-SW-COMPONENT-TYPE")
    composition = MagicMock(AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top")
    composition.Components.Elements = [MagicMock(ShortName="A", TypeTref=MagicMock(AUTOSARPathName="/SWA/A"))]
    composition.Ports.Elements = [MagicMock()] if boundary else []
    connections = [MagicMock(AutosarType="DELEGATION-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Delegation")] if boundary else []
    composition.ConnectPortsOfComposition.return_value = MagicMock(Connections=connections, DelegationPorts=())
    package = MagicMock(AutosarType="AR-PACKAGE")
    package.Elements.AddNewCompositionSwComponentType.return_value = composition
    elements = {"/SWA": package, "/SWA/A": component}
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [elements[path]] if path in elements else []

    # act
    created = await srv.mcp.call_tool("create_composition", {
        "composition_path": "/SWA/Top", "component_paths": ["/SWA/A"],
    })
    creation_result = json.loads(created.content[0].text)
    assert creation_result["status"] == "success"
    elements[creation_result["path"]] = composition
    app.ActiveProject.GetElementsByARPath.reset_mock()
    connected = await srv.mcp.call_tool("connect_composition_ports_by_interface", {
        "composition_path": creation_result["path"], **matching_arguments,
    })

    # assert
    app.ActiveProject.GetElementsByARPath.assert_called_once_with(creation_result["path"])
    assert json.loads(connected.content[0].text) == {
        "status": "success", "path": "/SWA/Top", "connection_count": len(connections),
        "connections": [{"path": connector.AUTOSARPathName, "type": connector.AutosarType}
                        for connector in connections],
    }
    composition.ConnectPortsOfComposition.assert_called_once()
    configuration = composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsInterfaceTypeStrategyChecked is True
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False


@pytest.mark.anyio
async def test_connect_composition_ports_mcp_rejects_invalid_schema(app):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports_by_interface", {
            "composition_path": "/SWA/Top", "strategy": "unknown",
        })
    assert not app.mock_calls


@pytest.mark.anyio
async def test_connect_composition_ports_by_port_name_mcp_workflow(app):
    # arrange
    composition = MagicMock(AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top")
    composition.ConnectPortsOfComposition.return_value = MagicMock(Connections=(), DelegationPorts=())
    app.ActiveProject.GetElementsByARPath.return_value = [composition]

    # act
    result = await srv.mcp.call_tool("connect_composition_ports_by_port_name", {
        "composition_path": "/SWA/Top", "source_expression": "P_<equal>", "target_expression": "R_<equal>",
    })

    # assert
    assert json.loads(result.content[0].text) == {
        "status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": [],
    }
    configuration = composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsPortNameStrategyChecked is True
    assert configuration.IsInterfaceNameStrategyChecked is False
    assert configuration.IsInterfaceTypeStrategyChecked is False
    assert configuration.SourceExpression == "P_<equal>"
    assert configuration.TargetExpression == "R_<equal>"


@pytest.mark.anyio
@pytest.mark.parametrize("tool,arguments", [
    ("connect_composition_ports_by_interface", {"strategy": "port_name"}),
    ("connect_composition_ports_by_interface", {"source_expression": "P_<equal>", "target_expression": "R_<equal>"}),
    ("connect_composition_ports_by_port_name", {}),
    ("connect_composition_ports_by_port_name", {"source_expression": "P_<equal>"}),
    ("connect_composition_ports_by_port_name", {"source_expression": "", "target_expression": "R_<equal>"}),
    ("connect_composition_ports_by_port_name", {
        "strategy": "port_name", "source_expression": "P_<equal>", "target_expression": "R_<equal>",
    }),
])
async def test_connect_composition_ports_mcp_rejects_invalid_combinations(app, tool, arguments):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool(tool, {"composition_path": "/SWA/Top", **arguments})
    assert not app.mock_calls


@pytest.mark.anyio
async def test_tool_schemas_have_no_top_level_combinators():
    # Some clients reject tool input schemas that start with a combinator.
    tools = await _registered_function_tools(srv.mcp)

    for tool in tools:
        assert not {"oneOf", "anyOf", "allOf", "not", "enum"} & tool.parameters.keys(), (
            f"Tool '{tool.name}': input schema must not use top-level combinators."
        )
        assert tool.parameters.get("type") == "object"


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
