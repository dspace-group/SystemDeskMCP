"""Tests for autosar_composition behavior."""

import inspect
from itertools import combinations
from unittest.mock import MagicMock
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import autosar_composition
from systemdesk_mcp import runtime
from tests.helpers import ASSEMBLY_ENDPOINTS, DELEGATION_ENDPOINTS, make_connector, validate_args


STRATEGIES = ("common_name_pattern", "common_interface_names", "common_interface_types")
SELECTIONS = [list(selection) for size in range(1, 4) for selection in combinations(STRATEGIES, size)]
TYPE_SELECTIONS = [selection for selection in SELECTIONS if "common_interface_types" in selection]


def pattern_options(strategies):
    """Return the model needed by direct calls selecting name matching."""
    return autosar_composition.CommonNamePatternOptions(
        pport_expression="P_<equal>", rport_expression="R_<equal>",
    ) if "common_name_pattern" in strategies else None


@pytest.mark.parametrize("model,arguments", [
    (autosar_composition.CommonNamePatternOptions,
     {"pport_expression": "P", "rport_expression": "R", "unknown": True}),
    (autosar_composition.CommonNamePatternOptions, {"pport_expression": "P"}),
    (autosar_composition.CommonNamePatternOptions, {"rport_expression": "R"}),
    (autosar_composition.CommonNamePatternOptions, {}),
    (autosar_composition.ConnectionOptions, {"unknown": True}),
])
def test_port_matching_option_models_reject_invalid_fields(model, arguments):
    # act / assert
    with pytest.raises(ValidationError):
        model(**arguments)


@pytest.fixture(name="port_composition")
def fixture_port_composition(app):
    """Existing boundary-free composition with an empty matcher result."""
    composition = MagicMock(
        spec=["AutosarType", "AUTOSARPathName", "Ports", "CreatePortConnectorConfiguration",
              "ConnectPortsOfComposition", "Delete"],
        AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top",
    )
    composition.Ports.Elements = []
    composition.CreatePortConnectorConfiguration.return_value = MagicMock(spec=[
        "IsPortNameStrategyChecked", "IsInterfaceNameStrategyChecked",
        "IsInterfaceTypeStrategyChecked", "SourceExpression", "TargetExpression",
        "IsSingleConnectionsChecked", "IsUseAllInterfacesChecked", "IsUseSelectedInterfacesChecked",
        "IsCreateDelegationPortsChecked", "IsCreateSingleDelegationPortChecked",
        "IsClosedLoopConnectionsChecked", "IsConnectPortsWithoutInterfaceChecked", "SetInterfaceCollection",
    ])
    composition.ConnectPortsOfComposition.return_value = MagicMock(Connections=())
    app.ActiveProject.GetElementsByARPath.return_value = [composition]
    return composition


@pytest.mark.parametrize("strategies", SELECTIONS)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("single_only", [True, False])
@pytest.mark.parametrize("closed_loop", [True, False])
def test_connect_composition_ports_strategy_mapping(port_composition, strategies, reverse, single_only, closed_loop):
    # arrange
    port_composition.ConnectPortsOfComposition.return_value.Connections = (make_connector("/SWA/Top/Connection"),)
    options = pattern_options(strategies)

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=strategies[::-1] if reverse else strategies,
        common_name_pattern_options=options,
        connection_options=autosar_composition.ConnectionOptions(
            single_connections_only=single_only, allow_closed_loop_connections=closed_loop,
        ),
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 1,
        "connections": [{"path": "/SWA/Top/Connection", "type": "ASSEMBLY-SW-CONNECTOR", **ASSEMBLY_ENDPOINTS}],
    }
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsPortNameStrategyChecked == ("common_name_pattern" in strategies)
    assert configuration.IsInterfaceNameStrategyChecked == ("common_interface_names" in strategies)
    assert configuration.IsInterfaceTypeStrategyChecked == ("common_interface_types" in strategies)
    assert configuration.SourceExpression == (options.pport_expression if options else "")
    assert configuration.TargetExpression == (options.rport_expression if options else "")
    assert configuration.IsSingleConnectionsChecked is single_only
    assert configuration.IsUseAllInterfacesChecked is True
    assert configuration.IsUseSelectedInterfacesChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False
    assert configuration.IsClosedLoopConnectionsChecked is closed_loop
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    configuration.SetInterfaceCollection.assert_not_called()
    port_composition.CreatePortConnectorConfiguration.assert_called_once_with()
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("strategies", TYPE_SELECTIONS)
def test_connect_composition_ports_selected_interfaces(app, port_composition, strategies):
    # arrange
    interface_paths = ["/Interfaces/Speed", "/Interfaces/Torque"]
    interface = MagicMock(AutosarType="SENDER-RECEIVER-INTERFACE")
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [
        port_composition if path == "/SWA/Top" else interface
    ]

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=strategies,
        common_name_pattern_options=pattern_options(strategies), interface_paths=interface_paths,
        connection_options=autosar_composition.ConnectionOptions(allow_closed_loop_connections=True),
    )

    # assert
    assert result == {"status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": []}
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsClosedLoopConnectionsChecked is True
    assert configuration.IsUseAllInterfacesChecked is False
    assert configuration.IsUseSelectedInterfacesChecked is True
    configuration.SetInterfaceCollection.assert_called_once_with(interface_paths)
    port_composition.CreatePortConnectorConfiguration.assert_called_once_with()
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    assert [entry.args[0] for entry in app.ActiveProject.GetElementsByARPath.call_args_list] == [
        *interface_paths, "/SWA/Top",
    ]


@pytest.mark.parametrize("failure,code", [
    ("missing", "AUTOSAR_ELEMENT_NOT_FOUND"), ("ambiguous", "AMBIGUOUS_AUTOSAR_PATH"),
    ("type", "INVALID_AUTOSAR_ELEMENT_TYPE"),
])
@pytest.mark.parametrize("strategies", TYPE_SELECTIONS)
def test_connect_composition_ports_interfaces_checked_before_configuration(app, port_composition, failure, code, strategies):
    # arrange
    interface = MagicMock(AutosarType="SENDER-RECEIVER-INTERFACE")
    invalid_matches = {"missing": [], "ambiguous": [interface, interface], "type": [port_composition]}
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: (
        [interface] if path == "/Interfaces/Valid" else invalid_matches[failure]
    )

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=strategies,
        common_name_pattern_options=pattern_options(strategies),
        interface_paths=["/Interfaces/Valid", "/Interfaces/Invalid"],
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == code
    assert [entry.args[0] for entry in app.ActiveProject.GetElementsByARPath.call_args_list] == [
        "/Interfaces/Valid", "/Interfaces/Invalid",
    ]
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()


@pytest.mark.parametrize("interface_type", [
    "SENDER-RECEIVER-INTERFACE", "CLIENT-SERVER-INTERFACE", "PARAMETER-INTERFACE",
    "MODE-SWITCH-INTERFACE", "TRIGGER-INTERFACE", "NV-DATA-INTERFACE",
])
def test_connect_composition_ports_interface_types(app, port_composition, interface_type):
    # arrange
    interface = MagicMock(AutosarType=interface_type)
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [
        port_composition if path == "/SWA/Top" else interface
    ]

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=["common_interface_types"],
        interface_paths=["/Interfaces/Selected"],
    )

    # assert
    assert result["status"] == "success"
    port_composition.ConnectPortsOfComposition.assert_called_once()


@pytest.mark.parametrize("arguments", [
    {"matching_strategies": []}, {"matching_strategies": ["unknown"]},
    {"matching_strategies": ["common_name_pattern"]},
    {"matching_strategies": ["common_name_pattern", "common_interface_names"]},
    {"matching_strategies": ["common_name_pattern", "common_interface_types"]},
    {"matching_strategies": list(STRATEGIES)},
    {"common_name_pattern_options": pattern_options(["common_name_pattern"])},
    {"matching_strategies": ["common_interface_names"],
     "common_name_pattern_options": pattern_options(["common_name_pattern"])},
    {"matching_strategies": ["common_interface_names", "common_interface_types"],
     "common_name_pattern_options": pattern_options(["common_name_pattern"])},
    {"matching_strategies": ["common_interface_names"], "interface_paths": ["/Interfaces/Speed"]},
    {"matching_strategies": ["common_name_pattern"], "interface_paths": ["/Interfaces/Speed"],
     "common_name_pattern_options": pattern_options(["common_name_pattern"])},
    {"matching_strategies": ["common_name_pattern", "common_interface_names"],
     "interface_paths": ["/Interfaces/Speed"],
     "common_name_pattern_options": pattern_options(["common_name_pattern"])},
])
def test_connect_composition_ports_invalid_configuration_before_mutation(app, port_composition, arguments):
    # act
    result = autosar_composition.connect_composition_ports(**{
        "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], **arguments,
    })

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    app.ActiveProject.GetElementsByARPath.assert_not_called()
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("strategies", SELECTIONS)
def test_connect_composition_ports_duplicate_selections_rejected(app, port_composition, strategies):
    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=[*strategies, strategies[0]],
        common_name_pattern_options=pattern_options(strategies),
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    app.ActiveProject.GetElementsByARPath.assert_not_called()
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()


@pytest.mark.parametrize("failure", ["setter", "factory", "matcher", "null", "readback"])
def test_connect_composition_ports_failure_preserves_model(app, port_composition, failure):
    # arrange
    interface = MagicMock(AutosarType="SENDER-RECEIVER-INTERFACE")
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [
        port_composition if path == "/SWA/Top" else interface
    ]
    result_object = port_composition.ConnectPortsOfComposition.return_value
    if failure == "setter":
        port_composition.CreatePortConnectorConfiguration.return_value.SetInterfaceCollection.side_effect = RuntimeError("selection failed")
    elif failure == "factory":
        port_composition.CreatePortConnectorConfiguration.side_effect = RuntimeError("factory failed")
    elif failure == "matcher":
        def partial_failure(_configuration):
            port_composition.Ports.Elements = [MagicMock()]
            raise RuntimeError("partially changed model")
        port_composition.ConnectPortsOfComposition.side_effect = partial_failure
    elif failure == "null":
        port_composition.ConnectPortsOfComposition.return_value = None
    else:
        result_object.Connections = [MagicMock(spec=["AutosarType", "Delete"], AutosarType="ASSEMBLY-SW-CONNECTOR")]

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=list(STRATEGIES),
        common_name_pattern_options=pattern_options(STRATEGIES), interface_paths=["/Interfaces/Speed"],
    )

    # assert
    started = failure not in {"factory", "setter"}
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert "details" in result
    assert ("model may have changed" in result["message"]) == started
    assert ("No rollback was attempted" in result["message"]) == started
    assert "passes" not in result
    assert port_composition.ConnectPortsOfComposition.call_count == started
    port_composition.CreatePortConnectorConfiguration.assert_called_once_with()
    port_composition.Delete.assert_not_called()
    for connector in result_object.Connections:
        connector.Delete.assert_not_called()
    if failure == "matcher":
        assert port_composition.Ports.Elements
        assert result["details"] == "partially changed model"
    elif failure == "null":
        assert result["details"] == "SystemDesk returned no port connector result."
    elif failure == "setter":
        assert result["details"] == "selection failed"


@pytest.mark.parametrize("boundary_type", ["P-PORT-PROTOTYPE", "R-PORT-PROTOTYPE"])
def test_connect_composition_ports_existing_boundary_ports(port_composition, boundary_type):
    # arrange
    boundary_port = MagicMock(AutosarType=boundary_type)
    port_composition.Ports.Elements = [boundary_port]
    connectors = [make_connector("/SWA/Top/Assembly"),
                  make_connector("/SWA/Top/Delegation", "DELEGATION-SW-CONNECTOR")]
    endpoints = DELEGATION_ENDPOINTS
    if boundary_type == "R-PORT-PROTOTYPE":
        connectors[1].LeftComponentPrototype = MagicMock(ShortName="SpeedSensor")
        connectors[1].RightComponentPrototype = None
        endpoints = {"provider": {"component": "SpeedSensor", "port": "PpSpeed"},
                     "requester": {"component": None, "port": "RpSpeed"}}
    port_composition.ConnectPortsOfComposition.return_value.Connections = tuple(connectors)

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=["common_interface_types"],
        connection_options=autosar_composition.ConnectionOptions(single_connections_only=False),
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 2,
        "connections": [
            {"path": "/SWA/Top/Assembly", "type": "ASSEMBLY-SW-CONNECTOR", **ASSEMBLY_ENDPOINTS},
            {"path": "/SWA/Top/Delegation", "type": "DELEGATION-SW-CONNECTOR", **endpoints},
        ],
    }
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsSingleConnectionsChecked is False
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    assert port_composition.Ports.Elements == [boundary_port]
    boundary_port.Delete.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("connection_options", [None, autosar_composition.ConnectionOptions()])
def test_connect_composition_ports_defaults(port_composition, connection_options):
    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=["common_interface_types"],
        connection_options=connection_options,
    )

    # assert
    assert result["status"] == "success"
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsInterfaceTypeStrategyChecked is True
    assert configuration.IsInterfaceNameStrategyChecked is False
    assert configuration.IsPortNameStrategyChecked is False
    assert configuration.SourceExpression == ""
    assert configuration.TargetExpression == ""
    assert configuration.IsSingleConnectionsChecked is True
    assert configuration.IsClosedLoopConnectionsChecked is False
    assert configuration.IsUseAllInterfacesChecked is True
    assert configuration.IsUseSelectedInterfacesChecked is False
    configuration.SetInterfaceCollection.assert_not_called()


def test_connect_composition_ports_positional_parameter_order(app, port_composition):
    # arrange
    signature = inspect.signature(autosar_composition.connect_composition_ports)
    interface = MagicMock(AutosarType="SENDER-RECEIVER-INTERFACE")
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: [
        port_composition if path == "/SWA/Top" else interface
    ]

    # act
    result = autosar_composition.connect_composition_ports(
        "/SWA/Top", list(STRATEGIES), pattern_options(STRATEGIES), ["/Interfaces/Speed"],
        autosar_composition.ConnectionOptions(single_connections_only=False, allow_closed_loop_connections=True), 60,
    )

    # assert
    assert list(signature.parameters) == [
        "composition_path", "matching_strategies", "common_name_pattern_options",
        "interface_paths", "connection_options", "timeout",
    ]
    assert signature.parameters["composition_path"].default is inspect.Parameter.empty
    assert signature.parameters["matching_strategies"].default is inspect.Parameter.empty
    assert signature.parameters["common_name_pattern_options"].default is None
    assert signature.parameters["interface_paths"].default == []
    assert signature.parameters["connection_options"].default is None
    assert signature.parameters["timeout"].default == 60
    assert result["status"] == "success"
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.SourceExpression == "P_<equal>"
    assert configuration.TargetExpression == "R_<equal>"
    assert configuration.IsSingleConnectionsChecked is False
    assert configuration.IsClosedLoopConnectionsChecked is True
    configuration.SetInterfaceCollection.assert_called_once_with(["/Interfaces/Speed"])
    port_composition.CreatePortConnectorConfiguration.assert_called_once_with()
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)


@pytest.mark.parametrize("strategies", SELECTIONS)
def test_connect_composition_ports_repeat_and_fresh_configuration(port_composition, strategies):
    # arrange
    configurations = [MagicMock(), MagicMock()]
    port_composition.CreatePortConnectorConfiguration.side_effect = configurations
    connector = make_connector("/SWA/Top/Connection")
    port_composition.ConnectPortsOfComposition.side_effect = [MagicMock(Connections=[connector]), MagicMock(Connections=())]

    # act
    first, second = [autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=strategies,
        common_name_pattern_options=pattern_options(strategies), interface_paths=[],
    ) for _ in range(2)]

    # assert
    assert first == {
        "status": "success", "path": "/SWA/Top", "connection_count": 1,
        "connections": [{"path": "/SWA/Top/Connection", "type": "ASSEMBLY-SW-CONNECTOR", **ASSEMBLY_ENDPOINTS}],
    }
    assert second == {"status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": []}
    assert port_composition.CreatePortConnectorConfiguration.call_count == 2
    assert [entry.args[0] for entry in port_composition.ConnectPortsOfComposition.call_args_list] == configurations
    for configuration in configurations:
        assert configuration.IsSingleConnectionsChecked is True
        assert configuration.IsUseAllInterfacesChecked is True
        assert configuration.IsUseSelectedInterfacesChecked is False
        configuration.SetInterfaceCollection.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("failure,code", [
    ("not_started", "SYSTEMDESK_NOT_RUNNING"), ("no_project", "NO_PROJECT_OPEN"),
    ("missing", "AUTOSAR_ELEMENT_NOT_FOUND"), ("ambiguous", "AMBIGUOUS_AUTOSAR_PATH"),
    ("type", "INVALID_AUTOSAR_ELEMENT_TYPE"),
])
def test_connect_composition_ports_prechecks(app, port_composition, failure, code):
    # arrange
    if failure == "not_started":
        runtime._application = None  # pylint: disable=protected-access
    elif failure == "no_project":
        app.ActiveProject = None
    elif failure == "missing":
        app.ActiveProject.GetElementsByARPath.return_value = []
    elif failure == "ambiguous":
        app.ActiveProject.GetElementsByARPath.return_value = [port_composition, port_composition]
    elif failure == "type":
        port_composition.AutosarType = "APPLICATION-SW-COMPONENT-TYPE"

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=["common_interface_types"],
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == code
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("arguments", [
    {"composition_path": "/SWA"}, {"composition_path": "relative/Top"},
    {"matching_strategies": []}, {"matching_strategies": None},
    {"matching_strategies": "common_interface_types"}, {"matching_strategies": ["unknown"]},
    {"matching_strategies": [None]}, {"matching_strategies": [42]},
    {"matching_strategies": ["interface_type"]}, {"matching_strategies": ["combined"]},
    {"interface_paths": None}, {"interface_paths": ["relative"]},
    {"interface_paths": ["/Interfaces"]}, {"interface_paths": [42]},
    {"interface_paths": "/Interfaces/Speed"}, {"timeout": 0}, {"timeout": 3601},
    {"common_name_pattern_options": {}},
    {"common_name_pattern_options": {"pport_expression": "P"}},
    {"common_name_pattern_options": {"rport_expression": "R"}},
    {"common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R", "unknown": True}},
    {"connection_options": {"unknown": True}}, {"connection_options": []},
    {"common_name_pattern_options": "P"},
])
def test_connect_composition_ports_invalid_schema(arguments):
    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.connect_composition_ports, **{
            "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], **arguments,
        })


@pytest.mark.parametrize("missing", ["composition_path", "matching_strategies"])
def test_connect_composition_ports_required_schema(missing):
    # arrange
    arguments = {"composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"]}
    del arguments[missing]

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.connect_composition_ports, **arguments)


@pytest.mark.parametrize("field", ["pport_expression", "rport_expression"])
@pytest.mark.parametrize("value", ["", None, 42, False, "X" * 257])
def test_connect_composition_ports_expression_constraints(field, value):
    # arrange
    options = {"pport_expression": "P", "rport_expression": "R", field: value}

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(
            autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
            matching_strategies=["common_name_pattern"], common_name_pattern_options=options,
        )


@pytest.mark.parametrize("field", ["single_connections_only", "allow_closed_loop_connections"])
@pytest.mark.parametrize("value", [None, "invalid", [], {}, 2])
def test_connect_composition_ports_invalid_boolean_schema(field, value):
    # act / assert
    with pytest.raises(ValidationError):
        validate_args(
            autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
            matching_strategies=["common_interface_types"], connection_options={field: value},
        )


@pytest.mark.parametrize("strategies", SELECTIONS)
@pytest.mark.parametrize("timeout", [1, 3600])
@pytest.mark.parametrize("connection_options", [None, {}, {"single_connections_only": False, "allow_closed_loop_connections": True}])
def test_connect_composition_ports_valid_schema(strategies, timeout, connection_options):
    # arrange
    options = pattern_options(strategies)

    # act / assert
    validate_args(
        autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
        matching_strategies=strategies, common_name_pattern_options=options.model_dump() if options else None,
        connection_options=connection_options, interface_paths=[], timeout=timeout,
    )


@pytest.mark.parametrize("arguments", [
    {"matching_strategies": ["common_interface_types", "common_interface_types"]},
    {"matching_strategies": ["common_name_pattern"]},
    {"common_name_pattern_options": {"pport_expression": "P", "rport_expression": "R"}},
    {"matching_strategies": ["common_interface_names"], "interface_paths": ["/Interfaces/Speed"]},
])
def test_connect_composition_ports_schema_leaves_dependencies_to_runtime(arguments):
    # act / assert
    validate_args(autosar_composition.connect_composition_ports, **{
        "composition_path": "/SWA/Top", "matching_strategies": ["common_interface_types"], **arguments,
    })


@pytest.mark.parametrize("expression", ["P", "X" * 256, "(", "[", "*", "no_such_port"])
def test_connect_composition_ports_expressions_pass_through(port_composition, expression):
    # arrange
    options = {"pport_expression": expression, "rport_expression": expression}

    # act
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
                  matching_strategies=["common_name_pattern"], common_name_pattern_options=options)
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=["common_name_pattern"],
        common_name_pattern_options=autosar_composition.CommonNamePatternOptions(**options),
    )

    # assert
    assert result == {"status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": []}
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.SourceExpression == expression
    assert configuration.TargetExpression == expression
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)


@pytest.mark.parametrize("strategies", SELECTIONS)
@pytest.mark.parametrize("timeout", [None, 1])
def test_connect_composition_ports_timeout_single_submission(monkeypatch, port_composition, strategies, timeout):
    # arrange
    executor = MagicMock()
    future = executor.submit.return_value
    future.result.side_effect = FutureTimeoutError()
    monkeypatch.setattr(runtime, "_com_executor", executor)

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", matching_strategies=strategies,
        common_name_pattern_options=pattern_options(strategies),
        **({"timeout": timeout} if timeout is not None else {}),
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "TIMEOUT"
    assert "get_systemdesk_status" in result["message"]
    executor.submit.assert_called_once()
    future.result.assert_called_once_with(timeout=60 if timeout is None else timeout)
    future.cancel.assert_not_called()
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()

    # act
    submission = executor.submit.call_args
    completed = submission.args[0](*submission.args[1:], **submission.kwargs)

    # assert
    assert completed == {"status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": []}
    port_composition.CreatePortConnectorConfiguration.assert_called_once_with()
    port_composition.ConnectPortsOfComposition.assert_called_once_with(
        port_composition.CreatePortConnectorConfiguration.return_value,
    )
    executor.submit.assert_called_once()
    future.cancel.assert_not_called()


def composition_model(project):
    """Install a stateful mock of the documented composition COM operations."""
    elements = {}

    def add_element(path, autosar_type):
        element = MagicMock(ShortName=path.rsplit("/", 1)[1],
                            AUTOSARPathName=path, AutosarType=autosar_type)
        elements[path] = element

        def delete():
            for candidate in list(elements):
                if candidate == path or candidate.startswith(path + "/"):
                    del elements[candidate]
            return True

        element.Delete.side_effect = delete
        element.ArPackages.AddNew.side_effect = lambda name: add_element(
            path + "/" + name, "AR-PACKAGE",
        )
        element.Elements.AddNewCompositionSwComponentType.side_effect = lambda name: add_element(
            path + "/" + name, "COMPOSITION-SW-COMPONENT-TYPE",
        )
        element.Components.Elements = []

        def add_component(component):
            names = {prototype.ShortName for prototype in element.Components.Elements}
            name = component.ShortName
            suffix = 1
            while name in names:
                name = component.ShortName + str(suffix)
                suffix += 1
            prototype = MagicMock(ShortName=name, TypeTref=component)
            element.Components.Elements.append(prototype)
            return prototype

        element.AddSwComponentType.side_effect = add_component
        return element

    project.GetElementsByARPath.side_effect = lambda path: (
        [elements[path]] if path in elements else []
    )
    project.RootAutosar.ArPackages.AddNew.side_effect = lambda name: add_element(
        "/" + name, "AR-PACKAGE",
    )
    add_element("/SWA", "AR-PACKAGE")
    add_element("/SWA/A", "APPLICATION-SW-COMPONENT-TYPE")
    add_element("/SWA/B", "APPLICATION-SW-COMPONENT-TYPE")
    return elements


def test_create_composition_success(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    package = elements["/SWA"]
    component = elements["/SWA/A"]

    # act
    result = autosar_composition.create_composition(
        composition_path="/SWA/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top",
        "components": [{"name": "A", "type": "/SWA/A"}],
    }
    package.Elements.AddNewCompositionSwComponentType.assert_called_once_with("Top")
    composition = elements["/SWA/Top"]
    composition.AddSwComponentType.assert_called_once_with(component)
    composition.Delete.assert_not_called()


def test_create_composition_nested_packages_and_unique_names(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    paths = ["/SWA/A", "/SWA/B", "/SWA/A"]

    # act
    result = autosar_composition.create_composition(
        composition_path="/Generated/Nested/Top", component_paths=paths,
    )

    # assert
    assert result["status"] == "success"
    assert result["path"] == "/Generated/Nested/Top"
    assert [item["type"] for item in result["components"]] == paths
    assert len({item["name"] for item in result["components"]}) == len(paths)
    assert elements["/Generated"].AutosarType == "AR-PACKAGE"
    assert elements["/Generated/Nested"].AutosarType == "AR-PACKAGE"
    composition = elements[result["path"]]
    assert [call.args[0] for call in composition.AddSwComponentType.call_args_list] == [
        elements[path] for path in paths
    ]
    for path in paths:
        elements[path].Delete.assert_not_called()


@pytest.mark.parametrize("component_type", ["APPLICATION-SW-COMPONENT-TYPE", "SERVICE-SW-COMPONENT-TYPE",])
def test_create_composition_accepts_different_component_types(app, component_type):
    # arrange
    elements = composition_model(app.ActiveProject)
    elements["/SWA/A"].AutosarType = component_type

    # act
    result = autosar_composition.create_composition(
        composition_path="/SWA/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result["status"] == "success"


@pytest.mark.parametrize("failure,code,path", [
    ("missing", "AUTOSAR_ELEMENT_NOT_FOUND", "/Missing/Component"),
    ("ambiguous", "AMBIGUOUS_AUTOSAR_PATH", "/SWA/A"),
    ("type", "INVALID_AUTOSAR_ELEMENT_TYPE", "/SWA/B"),
    ("occupied", "AUTOSAR_ELEMENT_ALREADY_EXISTS", "/Generated/Top"),
    ("parent", "INVALID_AUTOSAR_ELEMENT_TYPE", "/Generated"),
    ("ambiguous_parent", "AMBIGUOUS_AUTOSAR_PATH", "/Generated"),
])
def test_create_composition_prechecks_do_not_modify_model(app, failure, code, path):
    # arrange
    project = app.ActiveProject
    elements = composition_model(project)
    if failure == "type":
        elements[path].AutosarType = "SENDER-RECEIVER-INTERFACE"
    elif failure in ("occupied", "parent"):
        elements[path] = MagicMock(AutosarType="APPLICATION-SW-COMPONENT-TYPE")
    elif failure in ("ambiguous", "ambiguous_parent"):
        lookup = project.GetElementsByARPath.side_effect
        project.GetElementsByARPath.side_effect = lambda candidate: (
            [MagicMock(), MagicMock()] if candidate == path else lookup(candidate)
        )
    before = dict(elements)
    selected = ["/SWA/A", path] if failure in {"missing", "type"} else ["/SWA/A"]

    # act
    result = autosar_composition.create_composition(
        composition_path="/Generated/Top", component_paths=selected,
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == code
    assert path in result["message"]
    assert elements == before
    project.RootAutosar.ArPackages.AddNew.assert_not_called()
    for element in before.values():
        element.ArPackages.AddNew.assert_not_called()
        element.Elements.AddNewCompositionSwComponentType.assert_not_called()
        element.Delete.assert_not_called()


def test_create_composition_deletes_composition_on_failure(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    # Without a ShortName property, the model's AddSwComponentType raises.
    elements["/SWA/A"] = MagicMock(spec=["AutosarType"], AutosarType="APPLICATION-SW-COMPONENT-TYPE")

    # act
    result = autosar_composition.create_composition(
        composition_path="/Generated/Nested/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert "/Generated/Nested/Top" not in elements


@pytest.mark.parametrize("valid_path", ["/SWA/A", "/a_1/B_", "/SWA/" + "A" * 128])
def test_create_composition_accepts_valid_path_schema(valid_path):
    # act / assert
    validate_args(autosar_composition.create_composition, composition_path=valid_path, component_paths=[valid_path])


@pytest.mark.parametrize("invalid_path", [
    "", "/", "SWA/A", "A", "/SWA", "/SWA//A", "/SWA/A/", "/SWA/../A",
    "C:/SWA/A", "\\SWA\\A", "/SWA/A B", "/SWA/1A", "/SWA/*", "/SWA/A\n",
    "/_SWA/A", "/SWA/_A", "/SWA/" + "A" * 129,
])
@pytest.mark.parametrize("parameter", ["composition_path", "component_paths"])
def test_create_composition_rejects_invalid_path_schema(invalid_path, parameter):
    # arrange
    arguments = {"composition_path": "/SWA/Top", "component_paths": ["/SWA/A"]}
    arguments[parameter] = [invalid_path] if parameter == "component_paths" else invalid_path

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.create_composition, **arguments)


@pytest.mark.parametrize("paths", [[], [None], [42], "/SWA/A"])
def test_create_composition_requires_nonempty_path_list(paths):
    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.create_composition, composition_path="/SWA/Top", component_paths=paths)
