"""Tests for server behavior."""
# pylint: disable=protected-access

import inspect
import json
import typing
from itertools import combinations
from unittest.mock import MagicMock

import pytest
from packaging.version import Version
from pydantic.fields import FieldInfo
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import FunctionTool

import systemdesk_mcp.server as srv
from systemdesk_mcp import runtime
from tests.helpers import DELEGATION_ENDPOINTS, make_connector


MATCHING_STRATEGIES = ("common_name_pattern", "common_interface_names", "common_interface_types")
MATCHING_SELECTIONS = [
    list(selection) for size in range(1, 4) for selection in combinations(MATCHING_STRATEGIES, size)
]


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", [False, True])
@pytest.mark.parametrize("strategies", MATCHING_SELECTIONS)
@pytest.mark.parametrize("reverse", [False, True])
async def test_connect_composition_ports_mcp_workflow(app, boundary, strategies, reverse):
    # arrange
    component = MagicMock(AutosarType="APPLICATION-SW-COMPONENT-TYPE")
    composition = MagicMock(AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top")
    composition.Components.Elements = [MagicMock(ShortName="A", TypeTref=MagicMock(AUTOSARPathName="/SWA/A"))]
    composition.Ports.Elements = [MagicMock()] if boundary else []
    connections = [make_connector("/SWA/Top/Delegation", "DELEGATION-SW-CONNECTOR")] if boundary else []
    composition.ConnectPortsOfComposition.return_value = MagicMock(Connections=connections)
    package = MagicMock(AutosarType="AR-PACKAGE")
    package.Elements.AddNewCompositionSwComponentType.return_value = composition
    elements = {"/SWA": package, "/SWA/A": component}
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [elements[path]] if path in elements else []

    # act
    created = await srv.mcp.call_tool("create_composition", {
        "composition_path": "/SWA/Top", "component_paths": ["/SWA/A"],
    })
    created = json.loads(created.content[0].text)
    assert created["status"] == "success"
    elements[created["path"]] = composition
    app.ActiveProject.GetElementsByARPath.reset_mock()
    connected = await srv.mcp.call_tool("connect_composition_ports", {
        "composition_path": created["path"],
        "matching_strategies": strategies[::-1] if reverse else strategies,
        "common_name_pattern_options": {
            "pport_expression": "P_<equal>", "rport_expression": "R_<equal>",
        } if "common_name_pattern" in strategies else None,
        "connection_options": {"single_connections_only": False, "allow_closed_loop_connections": True},
    })

    # assert
    app.ActiveProject.GetElementsByARPath.assert_called_once_with(created["path"])
    expected = {
        "status": "success", "path": "/SWA/Top", "connection_count": len(connections),
        "connections": [{"path": connector.AUTOSARPathName, "type": connector.AutosarType, **DELEGATION_ENDPOINTS}
                        for connector in connections],
    }
    assert json.loads(connected.content[0].text) == expected
    configuration = composition.CreatePortConnectorConfiguration.return_value
    composition.CreatePortConnectorConfiguration.assert_called_once_with()
    composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    assert configuration.IsInterfaceTypeStrategyChecked == ("common_interface_types" in strategies)
    assert configuration.IsInterfaceNameStrategyChecked == ("common_interface_names" in strategies)
    assert configuration.IsPortNameStrategyChecked == ("common_name_pattern" in strategies)
    assert configuration.SourceExpression == ("P_<equal>" if "common_name_pattern" in strategies else "")
    assert configuration.TargetExpression == ("R_<equal>" if "common_name_pattern" in strategies else "")
    assert configuration.IsSingleConnectionsChecked is False
    assert configuration.IsClosedLoopConnectionsChecked is True
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [
    {"matching_strategies": "common_interface_types"}, {"matching_strategies": None},
    {"matching_strategies": []}, {"matching_strategies": ["unknown"]},
    {"matching_strategies": [None]}, {"matching_strategies": [42]},
    {"composition_path": "/SWA"}, {"timeout": 0}, {"timeout": 3601},
    {"interface_paths": None}, {"interface_paths": ["relative"]},
    {"common_name_pattern_options": {}},
    {"common_name_pattern_options": {"pport_expression": "P"}},
    {"common_name_pattern_options": {"rport_expression": "R"}},
    {"common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R", "unknown": True}},
    {"connection_options": {"unknown": True}},
])
async def test_connect_composition_ports_mcp_rejects_invalid_schema(app, arguments):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports", {
            "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], **arguments,
        })
    assert not app.mock_calls


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["pport_expression", "rport_expression"])
@pytest.mark.parametrize("value", ["", None, 42, False, "X" * 257])
async def test_connect_composition_ports_mcp_rejects_invalid_expressions(app, field, value):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports", {
            "composition_path": "/SWA/Top", "matching_strategies": ["common_name_pattern"],
            "common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R", field: value},
        })
    assert not app.mock_calls


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["single_connections_only", "allow_closed_loop_connections"])
@pytest.mark.parametrize("value", [None, "invalid", [], {}, 2])
async def test_connect_composition_ports_mcp_rejects_invalid_booleans(app, field, value):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports", {
            "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"],
            "connection_options": {field: value},
        })
    assert not app.mock_calls


@pytest.mark.anyio
@pytest.mark.parametrize("connection_options", [None, {}])
async def test_connect_composition_ports_mcp_default_options(app, connection_options):
    # arrange
    composition = MagicMock(AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top")
    composition.ConnectPortsOfComposition.return_value = MagicMock(Connections=())
    app.ActiveProject.GetElementsByARPath.return_value = [composition]

    # act
    response = await srv.mcp.call_tool("connect_composition_ports", {
        "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_names"],
        "interface_paths": [], "connection_options": connection_options,
    })

    # assert
    assert json.loads(response.content[0].text) == {
        "status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": [],
    }
    configuration = composition.CreatePortConnectorConfiguration.return_value
    configuration.SetInterfaceCollection.assert_not_called()
    assert configuration.IsSingleConnectionsChecked is True
    assert configuration.IsClosedLoopConnectionsChecked is False
    assert configuration.IsUseAllInterfacesChecked is True
    assert configuration.IsUseSelectedInterfacesChecked is False
    composition.CreatePortConnectorConfiguration.assert_called_once_with()
    composition.ConnectPortsOfComposition.assert_called_once_with(configuration)


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["composition_path", "matching_strategies"])
async def test_connect_composition_ports_mcp_requires_arguments(app, missing):
    # arrange
    arguments = {"composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"]}
    del arguments[missing]

    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports", arguments)
    assert not app.mock_calls


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("strategy", "interface_type"), ("strategy", "combined"),
    ("source_expression", "P_<equal>"), ("target_expression", "R_<equal>"),
    ("interface_strategy", "interface_name"), ("single_connections_only", False),
    ("allow_closed_loop_connections", True),
])
async def test_connect_composition_ports_mcp_rejects_retired_flat_parameters(app, field, value):
    # act / assert
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("connect_composition_ports", {
            "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], field: value,
        })
    assert not app.mock_calls


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [
    {"matching_strategies": ["common_name_pattern"]},
    {"matching_strategies": ["common_name_pattern"], "common_name_pattern_options": None},
    {"matching_strategies": list(MATCHING_STRATEGIES)},
    {"matching_strategies": ["common_interface_types", "common_interface_types"]},
    {"common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R"}},
    {"matching_strategies": ["common_interface_names"], "interface_paths": ["/Interfaces/Speed"]},
    {"matching_strategies": ["common_name_pattern", "common_interface_names"],
     "common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R"},
     "interface_paths": ["/Interfaces/Speed"]},
])
async def test_connect_composition_ports_mcp_rejects_invalid_combinations(app, arguments):
    # act
    response = await srv.mcp.call_tool("connect_composition_ports", {
        "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], **arguments,
    })

    # assert
    result = json.loads(response.content[0].text)
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    app.ActiveProject.GetElementsByARPath.assert_not_called()
    assert not app.ActiveProject.mock_calls


@pytest.mark.anyio
async def test_connect_composition_ports_only_unified_tool_registered():
    # act
    tools = await _registered_function_tools(srv.mcp)

    # assert
    names = {tool.name for tool in tools}
    assert [tool.name for tool in tools if tool.name.startswith("connect_composition_ports")] == [
        "connect_composition_ports",
    ]
    assert "connect_composition_ports_by_interface" not in names
    assert "connect_composition_ports_by_port_name" not in names


@pytest.mark.anyio
async def test_connect_composition_ports_schema_nested_options_and_defaults():
    # act
    tools = await _registered_function_tools(srv.mcp)
    tool = next(tool for tool in tools if tool.name == "connect_composition_ports")

    # assert
    schema = tool.parameters
    properties = schema["properties"]
    assert schema["required"] == ["composition_path", "matching_strategies"]
    assert schema["additionalProperties"] is False
    assert set(properties) == {
        "composition_path", "matching_strategies", "common_name_pattern_options",
        "interface_paths", "connection_options", "timeout",
    }
    strategies = properties["matching_strategies"]
    assert strategies["type"] == "array"
    assert strategies["minItems"] == 1
    assert set(strategies["items"]["enum"]) == set(MATCHING_STRATEGIES)
    assert not strategies.get("uniqueItems", False)
    assert not {"if", "then", "else", "dependentRequired", "dependentSchemas"} & schema.keys()
    for name in ["common_name_pattern_options", "connection_options"]:
        field = properties[name]
        assert field["default"] is None
        assert {"type": "null"} in field["anyOf"]
        object_schema = next(branch for branch in field["anyOf"] if branch.get("type") != "null")
        if "$ref" in object_schema:
            object_schema = schema["$defs"][object_schema["$ref"].rsplit("/", 1)[1]]
        assert object_schema["type"] == "object"
        assert object_schema["additionalProperties"] is False
        if name == "common_name_pattern_options":
            assert object_schema["required"] == ["pport_expression", "rport_expression"]
            assert set(object_schema["properties"]) == {"pport_expression", "rport_expression"}
            for expression in object_schema["properties"].values():
                assert expression["type"] == "string"
                assert expression["minLength"] == 1
                assert expression["maxLength"] == 256
        else:
            assert not object_schema.get("required")
            assert set(object_schema["properties"]) == {"single_connections_only", "allow_closed_loop_connections"}
            for field_name, default in [("single_connections_only", True), ("allow_closed_loop_connections", False)]:
                assert object_schema["properties"][field_name]["type"] == "boolean"
                assert object_schema["properties"][field_name]["default"] is default
    interfaces = properties["interface_paths"]
    assert interfaces["type"] == "array"
    assert interfaces["default"] == []
    assert interfaces.get("minItems", 0) == 0
    assert interfaces["items"]["type"] == "string"
    assert interfaces["items"]["pattern"]
    assert properties["timeout"]["default"] == 60
    assert properties["timeout"]["minimum"] == 1
    assert properties["timeout"]["maximum"] == 3600


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
async def test_structured_examples_are_valid_json():
    tools = await _registered_function_tools(srv.mcp)

    for tool in tools:
        for name, prop in tool.parameters.get("properties", {}).items():
            example = prop.get("description", "").rpartition("Example: ")[2]
            if example.startswith(("[", "{")):
                try:
                    json.loads(example)
                except json.JSONDecodeError:
                    pytest.fail(f"Tool '{tool.name}': example of parameter '{name}' must be valid JSON.")


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
