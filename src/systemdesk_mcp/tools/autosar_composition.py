"""AUTOSAR composition creation and port connections."""

from enum import Enum
from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    AutosarElementPath,
    PermanentError,
    TimeoutSeconds,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_active_project, logged, on_com_thread


@mcp.tool(
    title="Create Composition",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    output_schema=None,
)
@logged
@on_com_thread
def create_composition(
    composition_path: Annotated[AutosarElementPath, Field(
        description=(
            "Absolute AUTOSAR path for the new composition, including its package. "
            "Missing packages are created. Example: \"/Generated/TopLevel\""
        ),
    )],
    component_paths: Annotated[list[AutosarElementPath], Field(
        min_length=1,
        description=(
            "Absolute AUTOSAR paths of existing SwComponentTypes, in prototype order. "
            "Duplicates create separate instances. Example: [\"/SWA/A\", \"/SWA/B\"]"
        ),
    )],
) -> dict[str, Any]:
    """Create a CompositionSwComponentType containing the selected component types.

    Preconditions: start_systemdesk must be called first; a project must be open.
    All component paths are checked before modifying the model.
    Never overwrites an existing element - repeated calls will fail because of this.
    Missing packages are created automatically.
    SystemDesk generates unique prototype names, including for duplicate selections.
    Returns 'path' and 'components' (list with 'name' and 'type'), alongside status.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: component path does not exist
    - permanent / AMBIGUOUS_AUTOSAR_PATH: component or composition parent path resolves to multiple elements
    - permanent / INVALID_AUTOSAR_ELEMENT_TYPE: component is not a SwComponentType
      or existing composition parent is not an ARPackage
    - permanent / AUTOSAR_ELEMENT_ALREADY_EXISTS: destination path is occupied
    - permanent / UNEXPECTED_ERROR: COM creation or result readback failed
    """
    composition = None
    try:
        project: Any = get_active_project()

        if project.GetElementsByARPath(composition_path):
            raise PermanentError(
                f"An element already exists at '{composition_path}'. Nothing was overwritten.",
                error_code=ErrorCode.AUTOSAR_ELEMENT_ALREADY_EXISTS,
            )

        components = [_resolve_component(project, path) for path in component_paths]
        *package_names, composition_name = composition_path.strip("/").split("/")

        parent = project.RootAutosar
        path = ""
        for name in package_names:
            path += "/" + name
            matches = project.GetElementsByARPath(path)
            if len(matches) > 1:
                raise PermanentError(
                    f"Parent path '{path}' identifies multiple elements.",
                    error_code=ErrorCode.AMBIGUOUS_AUTOSAR_PATH,
                )
            if matches and matches[0].AutosarType != "AR-PACKAGE":
                raise PermanentError(
                    f"Parent path '{path}' is not an ARPackage.",
                    error_code=ErrorCode.INVALID_AUTOSAR_ELEMENT_TYPE,
                )
            parent = matches[0] if matches else parent.ArPackages.AddNew(name)

        composition = parent.Elements.AddNewCompositionSwComponentType(composition_name)
        for component in components:
            composition.AddSwComponentType(component)

        result_components = [
            {"name": prototype.ShortName, "type": prototype.TypeTref.AUTOSARPathName}
            for prototype in composition.Components.Elements
        ]
        return success_response({
            "path": composition.AUTOSARPathName,
            "components": result_components,
        })
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if composition is not None:
            composition.Delete()
        return error_response(
            f"Failed to create composition '{composition_path}'.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


class PortConnectionStrategy(str, Enum):
    """Matching strategy for automatic connections between existing ports."""

    PORT_NAME = "port_name"
    INTERFACE_NAME = "interface_name"
    INTERFACE_TYPE = "interface_type"
    COMBINED = "combined"


class InterfaceMatchStrategy(str, Enum):
    """Expression-free subset of PortConnectionStrategy."""

    INTERFACE_NAME = "interface_name"
    INTERFACE_TYPE = "interface_type"


# Chosen limit, not a documented SystemDesk one.
_MAX_EXPRESSION_LENGTH = 256


@mcp.tool(
    title="Connect Composition Ports",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None,
)
@logged
@on_com_thread
def connect_composition_ports(  # pylint: disable=too-many-arguments
    composition_path: Annotated[AutosarElementPath, Field(
        description="Absolute AUTOSAR path of an existing composition. Example: '/SWA/Top'",
    )],
    strategy: Annotated[PortConnectionStrategy, Field(
        description="Matching strategy: interface_type (default), interface_name, port_name, or combined (name first, then interface). Example: 'combined'.",
    )] = PortConnectionStrategy.INTERFACE_TYPE,
    *,
    source_expression: Annotated[str | None, Field(
        min_length=1, max_length=_MAX_EXPRESSION_LENGTH,
        description=(
            "Provided-port name pattern; required for port_name and combined, otherwise omit. "
            "Macros: <SWC>, <Interface>, <equal>. Example: 'P_<equal>'."
        ),
    )] = None,
    target_expression: Annotated[str | None, Field(
        min_length=1, max_length=_MAX_EXPRESSION_LENGTH,
        description=(
            "Required-port name pattern; required for port_name and combined, otherwise omit. "
            "Macros: <SWC>, <Interface>, <equal>. Example: 'R_<equal>'."
        ),
    )] = None,
    interface_strategy: Annotated[InterfaceMatchStrategy | None, Field(
        description="Only for combined: interface_type (default) or interface_name for the second pass; otherwise omit. Example: 'interface_name'.",
    )] = None,
    single_connections_only: Annotated[bool, Field(
        description=(
            "True restricts matching to ports with exactly one possible connection; "
            "False allows multiple connections per port, subject to SystemDesk rules. Example: False"
        ),
    )] = True,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument
        description="Maximum seconds to wait; timed-out work continues. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """Connect existing open ports using SystemDesk's ConnectPortsOfComposition matcher.

    Preconditions: start_systemdesk must be called first; a project must be open.
    Connects inner component ports and existing composition boundary ports using
    assembly and delegation connectors. Strategy defaults to interface_type.
    port_name requires source_expression and target_expression; interface strategies
    accept neither expression. combined requires both expressions and runs port_name
    first, then interface_strategy (default interface_type) on remaining open ports.
    This is sequential matching, not a requirement that both strategies agree.
    interface_strategy is accepted only for combined. All arguments are checked
    before either pass mutates the model. All interfaces are used.
    Expressions are SystemDesk common name patterns (regular expressions), with
    <SWC>, <Interface>, and <equal> macros passed through unchanged. Syntax is not
    validated: malformed or non-matching patterns can succeed with zero connections.
    Set single_connections_only=False to allow multiple connections per port,
    subject to SystemDesk's interface and connection rules.
    New port creation, closed-loop
    connections and connections without interfaces are disabled.
    Returns path, connection_count and connections (path/type), alongside status.
    combined also returns passes, each with strategy and its result. On failure,
    stops without retrying or rolling back; completed passes and their known
    connections are included, but the failed pass may have made unreported changes.
    The timeout covers the entire operation; timed-out work, including subsequent
    passes, continues on the COM worker.
    No matches is success. Does not save, validate, retry, or roll back the model.
    Repeating a successful call with the same inputs and an unchanged model
    connects no additional ports and returns connection_count=0.
    On mutation/readback failure the model may have changed.
    After TIMEOUT, call get_systemdesk_status and wait for its response before
    inspecting the model or retrying. It queues behind the timed-out operation
    on the same COM worker, so receiving its response confirms that operation
    has finished. The status response does not report whether that operation
    succeeded; inspect the resulting model before deciding whether to retry.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: composition path does not exist
    - permanent / AMBIGUOUS_AUTOSAR_PATH: path identifies multiple elements
    - permanent / INVALID_AUTOSAR_ELEMENT_TYPE: element is not a composition
    - permanent / INVALID_PORT_CONNECTOR_CONFIGURATION: unsupported strategy or invalid argument combination
    - transient / TIMEOUT: wait timed out; operation continues
    - permanent / UNEXPECTED_ERROR: configuration, matching, or result readback failed
    """
    selected_interface = InterfaceMatchStrategy.INTERFACE_TYPE if interface_strategy is None else interface_strategy
    try:
        get_active_project()
        if strategy == PortConnectionStrategy.COMBINED:
            _check_port_connection_arguments(PortConnectionStrategy.PORT_NAME, source_expression, target_expression)
            _check_port_connection_arguments(selected_interface, None, None)
        else:
            _check_port_connection_arguments(strategy, source_expression, target_expression)
            if interface_strategy is not None:
                raise PermanentError(
                    "interface_strategy is accepted only for combined matching.",
                    error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
                )
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)

    if strategy != PortConnectionStrategy.COMBINED:
        return _connect_ports(composition_path, strategy, source_expression, target_expression, single_connections_only)

    passes = []
    connections = []
    for pass_strategy, source, target in (
        (PortConnectionStrategy.PORT_NAME, source_expression, target_expression),
        (selected_interface, None, None),
    ):
        result = _connect_ports(composition_path, pass_strategy, source, target, single_connections_only)
        passes.append({"strategy": pass_strategy, **result})
        if result["status"] == "error":
            return {**result, "path": composition_path, "connection_count": len(connections),
                    "connections": connections, "passes": passes}
        connections.extend(result["connections"])
    return success_response({
        "path": result["path"], "connection_count": len(connections),
        "connections": connections, "passes": passes,
    })


def _connect_ports(composition_path, strategy, source_expression, target_expression, single_connections_only):
    matching_started = False
    try:
        project = get_active_project()
        _check_port_connection_arguments(strategy, source_expression, target_expression)
        composition = _resolve_component(project, composition_path)
        if composition.AutosarType != "COMPOSITION-SW-COMPONENT-TYPE":
            raise PermanentError(
                f"Element at '{composition_path}' is not a composition.",
                error_code=ErrorCode.INVALID_AUTOSAR_ELEMENT_TYPE,
            )
        configuration = composition.CreatePortConnectorConfiguration()
        configuration.IsPortNameStrategyChecked = strategy == PortConnectionStrategy.PORT_NAME
        configuration.IsInterfaceNameStrategyChecked = strategy == PortConnectionStrategy.INTERFACE_NAME
        configuration.IsInterfaceTypeStrategyChecked = strategy == PortConnectionStrategy.INTERFACE_TYPE
        configuration.SourceExpression = source_expression or ""
        configuration.TargetExpression = target_expression or ""
        configuration.IsSingleConnectionsChecked = single_connections_only
        configuration.IsUseAllInterfacesChecked = True
        configuration.IsUseSelectedInterfacesChecked = False
        configuration.IsCreateDelegationPortsChecked = False
        configuration.IsCreateSingleDelegationPortChecked = False
        configuration.IsClosedLoopConnectionsChecked = False
        configuration.IsConnectPortsWithoutInterfaceChecked = False

        matching_started = True
        result = composition.ConnectPortsOfComposition(configuration)
        if result is None:
            raise RuntimeError("SystemDesk returned no port connector result.")
        if result.DelegationPorts:
            raise RuntimeError("SystemDesk unexpectedly created new delegation ports.")
        connections = []
        for connector in result.Connections:
            if connector.AutosarType not in {"ASSEMBLY-SW-CONNECTOR", "DELEGATION-SW-CONNECTOR"}:
                raise RuntimeError("SystemDesk unexpectedly returned an unsupported connector type.")
            connections.append({
                "path": connector.AUTOSARPathName,
                "type": connector.AutosarType,
            })
        return success_response({
            "path": composition.AUTOSARPathName,
            "connection_count": len(connections),
            "connections": connections,
        })
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        message = f"Failed to connect ports in '{composition_path}'."
        if matching_started:
            message += " The model may have changed; inspect it before retrying. No rollback was attempted."
        return error_response(
            message, error_type="permanent", error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


def _check_port_connection_arguments(strategy, source_expression, target_expression):
    if strategy == PortConnectionStrategy.PORT_NAME:
        valid = all(
            isinstance(expression, str) and 0 < len(expression) <= _MAX_EXPRESSION_LENGTH
            for expression in (source_expression, target_expression)
        )
    else:
        valid = (
            strategy in tuple(InterfaceMatchStrategy)
            and source_expression is None and target_expression is None
        )
    if not valid:
        raise PermanentError(
            "Select a valid strategy. port_name requires both expressions with 1-256 characters; "
            "interface_name and interface_type accept none.",
            error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
        )


def _resolve_component(project, path):
    matches = project.GetElementsByARPath(path)
    if not matches:
        raise PermanentError(
            f"No component found at '{path}'.", error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
        )
    if len(matches) != 1:
        raise PermanentError(
            f"Component path '{path}' identifies multiple elements.",
            error_code=ErrorCode.AMBIGUOUS_AUTOSAR_PATH,
        )
    component = matches[0]
    if not component.AutosarType.endswith("-SW-COMPONENT-TYPE"):
        raise PermanentError(
            f"Element at '{path}' is not a SwComponentType.",
            error_code=ErrorCode.INVALID_AUTOSAR_ELEMENT_TYPE,
        )
    return component
