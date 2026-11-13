"""Tests for autosar_composition behavior."""

from unittest.mock import MagicMock
from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import autosar_composition
from systemdesk_mcp import runtime
from tests.helpers import validate_args


@pytest.mark.parametrize("strategy", [
    strategy for strategy in autosar_composition.PortConnectionStrategy
    if strategy != autosar_composition.PortConnectionStrategy.COMBINED
])
@pytest.mark.parametrize("single_only", [True, False])
def test_connect_composition_ports_strategy_mapping(app, strategy, single_only):
    # arrange
    composition = MagicMock(AutosarType="COMPOSITION-SW-COMPONENT-TYPE", AUTOSARPathName="/SWA/Top")
    composition.Ports.Elements = []
    app.ActiveProject.GetElementsByARPath.return_value = [composition]
    connector = MagicMock(AutosarType="ASSEMBLY-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Connection")
    composition.ConnectPortsOfComposition.return_value = MagicMock(
        Connections=(connector,), DelegationPorts=(),
    )
    expressions = {"source_expression": "P_<equal>", "target_expression": "R_<equal>"} if strategy == "port_name" else {}

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy=strategy,
        single_connections_only=single_only, **expressions,
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 1,
        "connections": [{"path": "/SWA/Top/Connection", "type": "ASSEMBLY-SW-CONNECTOR"}],
    }
    configuration = composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsPortNameStrategyChecked == (strategy == "port_name")
    assert configuration.IsInterfaceNameStrategyChecked == (strategy == "interface_name")
    assert configuration.IsInterfaceTypeStrategyChecked == (strategy == "interface_type")
    assert configuration.SourceExpression == expressions.get("source_expression", "")
    assert configuration.TargetExpression == expressions.get("target_expression", "")
    assert configuration.IsSingleConnectionsChecked is single_only
    assert configuration.IsUseAllInterfacesChecked is True
    assert configuration.IsUseSelectedInterfacesChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False
    assert configuration.IsClosedLoopConnectionsChecked is False
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    composition.CreatePortConnectorConfiguration.assert_called_once_with()
    composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    composition.Delete.assert_not_called()


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
        "IsClosedLoopConnectionsChecked", "IsConnectPortsWithoutInterfaceChecked",
    ])
    composition.ConnectPortsOfComposition.return_value = MagicMock(
        spec=["Connections", "DelegationPorts"], Connections=(), DelegationPorts=(),
    )
    app.ActiveProject.GetElementsByARPath.return_value = [composition]
    return composition


@pytest.mark.parametrize("boundary_type", ["P-PORT-PROTOTYPE", "R-PORT-PROTOTYPE"])
def test_connect_composition_ports_existing_boundary_ports(port_composition, boundary_type):
    # arrange
    boundary_port = MagicMock(AutosarType=boundary_type)
    port_composition.Ports.Elements = [boundary_port]
    connectors = [
        MagicMock(AutosarType="ASSEMBLY-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Assembly"),
        MagicMock(AutosarType="DELEGATION-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Delegation"),
    ]
    port_composition.ConnectPortsOfComposition.return_value.Connections = tuple(connectors)

    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", strategy="interface_type")

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 2,
        "connections": [{"path": connector.AUTOSARPathName, "type": connector.AutosarType}
                        for connector in connectors],
    }
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    assert port_composition.Ports.Elements == [boundary_port]
    boundary_port.Delete.assert_not_called()
    port_composition.Delete.assert_not_called()


def test_connect_composition_ports_multiple_connections_enabled(port_composition):
    # arrange
    connectors = [
        MagicMock(AutosarType="ASSEMBLY-SW-CONNECTOR", AUTOSARPathName=f"/SWA/Top/Connection{index}")
        for index in range(2)
    ]
    port_composition.ConnectPortsOfComposition.return_value.Connections = tuple(connectors)

    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", single_connections_only=False)

    # assert
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsSingleConnectionsChecked is False
    port_composition.ConnectPortsOfComposition.assert_called_once_with(configuration)
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 2,
        "connections": [{"path": connector.AUTOSARPathName, "type": connector.AutosarType}
                        for connector in connectors],
    }
    assert configuration.IsConnectPortsWithoutInterfaceChecked is False
    assert configuration.IsCreateDelegationPortsChecked is False
    assert configuration.IsCreateSingleDelegationPortChecked is False


def test_connect_composition_ports_default_strategy(port_composition):
    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top")
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top")

    # assert
    assert result["status"] == "success"
    configuration = port_composition.CreatePortConnectorConfiguration.return_value
    assert configuration.IsInterfaceTypeStrategyChecked is True
    assert configuration.IsInterfaceNameStrategyChecked is False
    assert configuration.IsPortNameStrategyChecked is False
    assert configuration.SourceExpression == ""
    assert configuration.TargetExpression == ""


def test_connect_composition_ports_empty_and_fresh_configuration(port_composition):
    # arrange
    first = port_composition.CreatePortConnectorConfiguration.return_value
    second = MagicMock()
    port_composition.CreatePortConnectorConfiguration.side_effect = [first, second]

    # act
    results = [autosar_composition.connect_composition_ports(composition_path="/SWA/Top", strategy="interface_name")
               for _ in range(2)]

    # assert
    assert results == [{"status": "success", "path": "/SWA/Top",
                        "connection_count": 0, "connections": []}] * 2
    assert first.IsSingleConnectionsChecked is True
    assert second.IsSingleConnectionsChecked is True
    assert [call.args[0] for call in port_composition.ConnectPortsOfComposition.call_args_list] == [first, second]


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
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", strategy="interface_type")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == code
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("arguments", [
    {"strategy": "unknown"}, {"strategy": "port_name"},
    {"strategy": "port_name", "source_expression": "P_<equal>"},
    {"strategy": "port_name", "target_expression": "R_<equal>"},
    {"strategy": "port_name", "source_expression": None, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>"}, {"target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": "R_<equal>"},
    {"strategy": "interface_name", "source_expression": "P_<equal>"},
    {"interface_strategy": "interface_type"},
    {"strategy": "interface_name", "interface_strategy": "interface_name"},
    {"strategy": "port_name", "source_expression": "P_<equal>",
     "target_expression": "R_<equal>", "interface_strategy": "interface_type"},
])
def test_connect_composition_ports_invalid_configuration(port_composition, arguments):
    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", **arguments)

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()


@pytest.mark.parametrize("expressions", [
    {"source_expression": "", "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": ""},
    {"source_expression": 42, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": None},
    {"source_expression": "P" * 257, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": "R" * 257},
])
def test_connect_composition_ports_by_port_name_invalid_configuration(port_composition, expressions):
    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="port_name", **expressions,
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()


@pytest.mark.parametrize("failure", ["factory", "matcher", "null", "delegation", "type", "readback"])
def test_connect_composition_ports_failure_preserves_model(port_composition, failure):
    # arrange
    result_object = port_composition.ConnectPortsOfComposition.return_value
    if failure == "factory":
        port_composition.CreatePortConnectorConfiguration.side_effect = RuntimeError("factory failed")
    elif failure == "matcher":
        def partial_failure(_configuration):
            port_composition.Ports.Elements = [MagicMock()]
            raise RuntimeError("partially changed model")
        port_composition.ConnectPortsOfComposition.side_effect = partial_failure
    elif failure == "null":
        port_composition.ConnectPortsOfComposition.return_value = None
    elif failure == "delegation":
        result_object.DelegationPorts = [MagicMock()]
    elif failure == "type":
        result_object.Connections = [MagicMock(AutosarType="UNKNOWN-SW-CONNECTOR")]
    else:
        result_object.Connections = [MagicMock(spec=["AutosarType"], AutosarType="ASSEMBLY-SW-CONNECTOR")]

    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", strategy="interface_type")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert "details" in result
    assert ("model may have changed" in result["message"]) == (failure != "factory")
    assert port_composition.ConnectPortsOfComposition.call_count == (failure != "factory")
    port_composition.Delete.assert_not_called()
    if failure == "matcher":
        assert port_composition.Ports.Elements


@pytest.mark.parametrize("arguments", [
    {"composition_path": "/SWA"}, {"composition_path": "relative/Top"},
    {"strategy": "unknown"}, {"strategy": None},
    {"interface_strategy": "unknown"}, {"interface_strategy": ""},
    {"interface_strategy": "port_name"}, {"interface_strategy": "combined"},
    {"timeout": 0}, {"timeout": 3601},
])
def test_connect_composition_ports_invalid_schema(arguments):
    # arrange
    parameters = {"composition_path": "/SWA/Top", "strategy": "interface_type", **arguments}

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.connect_composition_ports, **parameters)


@pytest.mark.parametrize("strategy", ["interface_name", "interface_type"])
@pytest.mark.parametrize("timeout", [1, 3600])
def test_connect_composition_ports_valid_schema(strategy, timeout):
    # act / assert
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
                  strategy=strategy, timeout=timeout)


@pytest.mark.parametrize("arguments", [
    {"source_expression": "", "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": ""},
    {"source_expression": 42, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": 42},
    {"source_expression": "P" * 257, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": "R" * 257},
    {"source_expression": "P_<equal>", "target_expression": "R_<equal>", "timeout": 0},
    {"source_expression": "P_<equal>", "target_expression": "R_<equal>", "composition_path": "/SWA"},
])
def test_connect_composition_ports_by_port_name_invalid_schema(arguments):
    # arrange
    parameters = {"composition_path": "/SWA/Top", "strategy": "port_name", **arguments}

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar_composition.connect_composition_ports, **parameters)


@pytest.mark.parametrize("timeout", [1, 3600])
def test_connect_composition_ports_by_port_name_valid_schema(timeout):
    # act / assert
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top", strategy="port_name",
                  source_expression="P_<equal>", target_expression="R_<equal>", timeout=timeout)


def test_connect_composition_ports_by_port_name_accepts_maximum_length(port_composition):
    # arrange
    arguments = {"composition_path": "/SWA/Top", "strategy": "port_name",
                 "source_expression": "P" * 256, "target_expression": "R" * 256}

    # act
    validate_args(autosar_composition.connect_composition_ports, **arguments)
    result = autosar_composition.connect_composition_ports(**arguments)

    # assert
    assert result["status"] == "success"
    port_composition.ConnectPortsOfComposition.assert_called_once()


@pytest.mark.parametrize("expression", ["(", "[", "*", "no_such_port"])
def test_connect_composition_ports_by_port_name_non_matching_expression_succeeds(port_composition, expression):
    # arrange
    configuration = port_composition.CreatePortConnectorConfiguration.return_value

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="port_name",
        source_expression=expression, target_expression=expression,
    )

    # assert
    assert result == {"status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": []}
    assert configuration.SourceExpression == expression
    assert configuration.TargetExpression == expression


def test_connect_composition_ports_timeout_does_not_cancel(monkeypatch, app):
    # arrange
    executor = MagicMock()
    future = executor.submit.return_value
    future.result.side_effect = FutureTimeoutError()
    monkeypatch.setattr(runtime, "_com_executor", executor)

    # act
    result = autosar_composition.connect_composition_ports(composition_path="/SWA/Top", strategy="interface_type", timeout=1)

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "TIMEOUT"
    assert "get_systemdesk_status" in result["message"]
    future.result.assert_called_once_with(timeout=1)
    future.cancel.assert_not_called()
    assert not app.mock_calls


@pytest.mark.parametrize("interface_strategy", [None, "interface_name", "interface_type"])
def test_connect_composition_ports_combined(port_composition, interface_strategy):
    # arrange
    configurations = [MagicMock(), MagicMock()]
    port_composition.CreatePortConnectorConfiguration.side_effect = configurations
    connectors = [
        MagicMock(AutosarType="ASSEMBLY-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Named"),
        MagicMock(AutosarType="DELEGATION-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Interface"),
    ]
    port_composition.ConnectPortsOfComposition.side_effect = [
        MagicMock(Connections=(connector,), DelegationPorts=()) for connector in connectors
    ]

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined",
        source_expression="P_<equal>", target_expression="R_<equal>",
        single_connections_only=False,
        **({"interface_strategy": interface_strategy} if interface_strategy is not None else {}),
    )

    # assert
    expected_strategy = interface_strategy or "interface_type"
    expected_connections = [
        {"path": connector.AUTOSARPathName, "type": connector.AutosarType}
        for connector in connectors
    ]
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 2,
        "connections": expected_connections,
        "passes": [
            {"strategy": strategy, "status": "success", "path": "/SWA/Top",
             "connection_count": 1, "connections": [connection]}
            for strategy, connection in zip(["port_name", expected_strategy], expected_connections)
        ],
    }
    assert configurations[0].IsPortNameStrategyChecked is True
    assert configurations[0].IsInterfaceNameStrategyChecked is False
    assert configurations[0].IsInterfaceTypeStrategyChecked is False
    assert configurations[1].IsPortNameStrategyChecked is False
    assert configurations[1].IsInterfaceNameStrategyChecked == (expected_strategy == "interface_name")
    assert configurations[1].IsInterfaceTypeStrategyChecked == (expected_strategy == "interface_type")
    assert configurations[0].SourceExpression == "P_<equal>"
    assert configurations[0].TargetExpression == "R_<equal>"
    assert configurations[1].SourceExpression == ""
    assert configurations[1].TargetExpression == ""
    assert all(configuration.IsSingleConnectionsChecked is False for configuration in configurations)
    assert [call.args[0] for call in port_composition.ConnectPortsOfComposition.call_args_list] == configurations
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("arguments", [
    {}, {"source_expression": "P_<equal>"}, {"target_expression": "R_<equal>"},
    {"source_expression": None, "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": None},
    {"source_expression": "", "target_expression": "R_<equal>"},
    {"source_expression": "P_<equal>", "target_expression": ""},
    {"source_expression": 42, "target_expression": "R_<equal>"},
    {"source_expression": "P" * 257, "target_expression": "R_<equal>"},
    *[{"source_expression": "P_<equal>", "target_expression": "R_<equal>",
       "interface_strategy": strategy}
      for strategy in ["unknown", "port_name", "combined", "", 42]],
])
def test_connect_composition_ports_combined_invalid_before_mutation(app, port_composition, arguments):
    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined", **arguments,
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_PORT_CONNECTOR_CONFIGURATION"
    app.ActiveProject.GetElementsByARPath.assert_not_called()
    port_composition.CreatePortConnectorConfiguration.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()
    port_composition.Delete.assert_not_called()


@pytest.mark.parametrize("failed_pass", [0, 1])
def test_connect_composition_ports_combined_failure_stops(port_composition, failed_pass):
    # arrange
    connection = {"path": "/SWA/Top/Named", "type": "ASSEMBLY-SW-CONNECTOR"}
    first_result = MagicMock(
        Connections=(MagicMock(AUTOSARPathName=connection["path"], AutosarType=connection["type"]),),
        DelegationPorts=(),
    )
    failure = RuntimeError("partially changed model")
    port_composition.ConnectPortsOfComposition.side_effect = (
        [failure] if failed_pass == 0 else [first_result, failure]
    )

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined",
        source_expression="P_<equal>", target_expression="R_<equal>",
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert "model may have changed" in result["message"]
    assert result["details"] == str(failure)
    assert result["path"] == "/SWA/Top"
    assert result["connection_count"] == failed_pass
    assert result["connections"] == ([connection] if failed_pass else [])
    expected_passes = []
    if failed_pass:
        expected_passes.append({
            "strategy": "port_name", "status": "success", "path": "/SWA/Top",
            "connection_count": 1, "connections": [connection],
        })
    expected_passes.append({
        "strategy": "interface_type" if failed_pass else "port_name",
        "status": "error", "error_type": "permanent", "error_code": "UNEXPECTED_ERROR",
        "message": result["message"], "details": str(failure),
    })
    assert result["passes"] == expected_passes
    assert port_composition.CreatePortConnectorConfiguration.call_count == failed_pass + 1
    assert port_composition.ConnectPortsOfComposition.call_count == failed_pass + 1
    port_composition.Delete.assert_not_called()


def test_connect_composition_ports_combined_zero_name_matches(port_composition):
    # arrange
    connection = {"path": "/SWA/Top/Interface", "type": "DELEGATION-SW-CONNECTOR"}
    port_composition.ConnectPortsOfComposition.side_effect = [
        MagicMock(Connections=(), DelegationPorts=()),
        MagicMock(Connections=(MagicMock(AUTOSARPathName=connection["path"],
                                        AutosarType=connection["type"]),), DelegationPorts=()),
    ]

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined",
        source_expression="no_such_port", target_expression="no_such_port",
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top", "connection_count": 1,
        "connections": [connection],
        "passes": [
            {"strategy": "port_name", "status": "success", "path": "/SWA/Top",
             "connection_count": 0, "connections": []},
            {"strategy": "interface_type", "status": "success", "path": "/SWA/Top",
             "connection_count": 1, "connections": [connection]},
        ],
    }
    assert port_composition.ConnectPortsOfComposition.call_count == 2


def test_connect_composition_ports_combined_repeat_adds_no_connections(port_composition):
    # arrange
    configurations = [MagicMock() for _ in range(4)]
    port_composition.CreatePortConnectorConfiguration.side_effect = configurations
    remaining = {
        "port_name": [MagicMock(AutosarType="ASSEMBLY-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Named")],
        "interface_type": [MagicMock(AutosarType="DELEGATION-SW-CONNECTOR", AUTOSARPathName="/SWA/Top/Interface")],
    }

    def match_open_ports(configuration):
        strategy = "port_name" if configuration.IsPortNameStrategyChecked else "interface_type"
        return MagicMock(Connections=remaining.pop(strategy, []), DelegationPorts=())

    port_composition.ConnectPortsOfComposition.side_effect = match_open_ports

    # act
    first, second = [autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined",
        source_expression="P_<equal>", target_expression="R_<equal>",
    ) for _ in range(2)]

    # assert
    assert first["status"] == "success"
    assert first["connection_count"] == 2
    assert second == {
        "status": "success", "path": "/SWA/Top", "connection_count": 0, "connections": [],
        "passes": [{"strategy": strategy, "status": "success", "path": "/SWA/Top",
                    "connection_count": 0, "connections": []}
                   for strategy in ["port_name", "interface_type"]],
    }
    assert [entry.args[0] for entry in port_composition.ConnectPortsOfComposition.call_args_list] == configurations
    assert not remaining
    port_composition.Delete.assert_not_called()


def test_connect_composition_ports_combined_timeout_single_submission(monkeypatch, port_composition):
    # arrange
    executor = MagicMock()
    future = executor.submit.return_value
    future.result.side_effect = FutureTimeoutError()
    monkeypatch.setattr(runtime, "_com_executor", executor)

    # act
    result = autosar_composition.connect_composition_ports(
        composition_path="/SWA/Top", strategy="combined",
        source_expression="P_<equal>", target_expression="R_<equal>", timeout=1,
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "TIMEOUT"
    assert "get_systemdesk_status" in result["message"]
    executor.submit.assert_called_once()
    future.result.assert_called_once_with(timeout=1)
    future.cancel.assert_not_called()
    port_composition.ConnectPortsOfComposition.assert_not_called()

    # act
    submission = executor.submit.call_args
    completed = submission.args[0](*submission.args[1:], **submission.kwargs)

    # assert
    assert completed["status"] == "success"
    assert [entry["strategy"] for entry in completed["passes"]] == ["port_name", "interface_type"]
    assert port_composition.ConnectPortsOfComposition.call_count == 2
    executor.submit.assert_called_once()


@pytest.mark.parametrize("strategy", list(autosar_composition.PortConnectionStrategy))
def test_connect_composition_ports_nullable_optional_expressions_schema(strategy):
    # act / assert
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
                  strategy=strategy, source_expression=None, target_expression=None,
                  interface_strategy=None)


@pytest.mark.parametrize("interface_strategy", [None, "interface_name", "interface_type"])
def test_connect_composition_ports_combined_valid_schema(interface_strategy):
    # act / assert
    validate_args(autosar_composition.connect_composition_ports, composition_path="/SWA/Top",
                  strategy="combined", source_expression="P_<equal>", target_expression="R_<equal>",
                  interface_strategy=interface_strategy)


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
