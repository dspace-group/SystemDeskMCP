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
from systemdesk_mcp.tools.autosar import resolve_component
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
    After success, use connect_composition_ports with the returned 'path' as
    composition_path to match open ports. Do not connect if creation failed.

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

        components = [resolve_component(project, path) for path in component_paths]
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


class _PortConnectionStrategy(str, Enum):
    """Matching strategy for automatic connections between existing ports."""

    PORT_NAME = "port_name"
    INTERFACE_NAME = "interface_name"
    INTERFACE_TYPE = "interface_type"
    COMBINED = "combined"


class _InterfaceMatchStrategy(str, Enum):
    """Match ports by interface name or type in the second pass of combined matching."""

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
def connect_composition_ports(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    composition_path: Annotated[AutosarElementPath, Field(
        description="Absolute AUTOSAR path of an existing composition. Example: '/SWA/Top'",
    )],
    strategy: Annotated[_PortConnectionStrategy, Field(
        description="Matching strategy: interface_type (default), interface_name, port_name, or combined (name first, then interface). Example: 'combined'.",
    )] = _PortConnectionStrategy.INTERFACE_TYPE,
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
    interface_strategy: Annotated[_InterfaceMatchStrategy | None, Field(
        description="Only for combined: interface_type (default) or interface_name for the second pass; otherwise omit. Example: 'interface_name'.",
    )] = None,
    single_connections_only: Annotated[bool, Field(
        description=(
            "True restricts matching to ports with exactly one possible connection; "
            "False allows multiple connections per port, subject to SystemDesk rules. Example: False"
        ),
    )] = True,
    allow_closed_loop_connections: Annotated[bool, Field(
        description="Allow closed-loop connections during matching. Example: True",
    )] = False,
    interface_paths: Annotated[list[AutosarElementPath] | None, Field(
        min_length=1,
        description=(
            "Interfaces to use for interface_type matching; omit to use all. "
            "For combined, applies only to its interface_type pass. Example: [\"/Interfaces/Speed\"]"
        ),
    )] = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument # used by @on_com_thread
        description="Maximum seconds to wait; timed-out work continues. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """Automatically connect compatible ports within an existing AUTOSAR composition.

    Preconditions: start_systemdesk must be called first; a project must be open.

    Use after creating or importing a composition to match open ports. After a
    successful create_composition call, pass its returned 'path' as composition_path.
    Do not connect if creation failed.

    Connects ports between component instances using assembly connectors and links
    component ports to existing composition boundary ports using delegation
    connectors. Creates connections only; does not create new ports.

    Ports are matched by port-name patterns, interface name, or interface type.
    The default strategy is interface_type. The combined strategy first matches
    by port-name patterns, then uses the selected interface strategy to connect
    remaining open ports.

    port_name requires source_expression and target_expression; interface strategies
    accept neither expression. combined requires both expressions and runs port_name
    first, then interface_strategy (default interface_type) on remaining open ports.
    This is sequential matching, not a requirement that both strategies agree.
    interface_strategy is accepted only for combined. All arguments are checked
    before either pass mutates the model. interface_paths optionally restricts
    interface_type matching to existing port interfaces; omit to use all interfaces.
    It is accepted only for interface_type or combined with an interface_type
    second pass, and does not restrict combined's initial port_name pass.
    Expressions are SystemDesk common name patterns (regular expressions), with
    <SWC> as software component name, <Interface> as interface name, and <equal> which specifies
    a part that has to be identical in both port names. Syntax is not
    validated: malformed or non-matching patterns can succeed with zero connections.
    New port creation and connections without interfaces are disabled.
    Returns path, connection_count and connections, alongside status. Each connection
    includes path, type, provider and requester. Endpoints contain component and port
    prototype names; component is null for a composition boundary endpoint.
    combined also returns passes, each with strategy and its result. On failure,
    stops without retrying or rolling back; completed passes and their known
    connections are included, but the failed pass may have made unreported changes.
    The timeout covers the entire operation; timed-out work, including subsequent
    passes, continues on the COM worker.
    No matches is success. Does not save, validate, retry, or roll back the model.
    Repeating a successful call with the same inputs and an unchanged model
    connects no additional ports and returns connection_count=0.
    If creating connections or reading the results fails, some connections may
    already exist. Changes are not undone; inspect the composition before retrying.
    Validate and save separately after connecting ports.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: composition or interface path does not exist
    - permanent / AMBIGUOUS_AUTOSAR_PATH: path identifies multiple elements
    - permanent / INVALID_AUTOSAR_ELEMENT_TYPE: element is not a composition or port interface
    - permanent / INVALID_PORT_CONNECTOR_CONFIGURATION: unsupported strategy or invalid argument combination
    - transient / TIMEOUT: wait timed out; operation continues
    - permanent / UNEXPECTED_ERROR: configuration, matching, or result readback failed
    """
    selected_interface = _InterfaceMatchStrategy.INTERFACE_TYPE if interface_strategy is None else interface_strategy
    try:
        project = get_active_project()
        if strategy == _PortConnectionStrategy.COMBINED:
            _check_port_connection_arguments(_PortConnectionStrategy.PORT_NAME, source_expression, target_expression)
        else:
            _check_port_connection_arguments(strategy, source_expression, target_expression)
            if interface_strategy is not None:
                raise PermanentError(
                    "interface_strategy is accepted only for combined matching.",
                    error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
                )
        if interface_paths is not None:
            effective_strategy = selected_interface if strategy == _PortConnectionStrategy.COMBINED else strategy
            if effective_strategy != _PortConnectionStrategy.INTERFACE_TYPE or not interface_paths:
                raise PermanentError(
                    "interface_paths requires a nonempty list and interface_type matching.",
                    error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
                )
            _check_interface_paths(project, interface_paths)
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)

    if strategy != _PortConnectionStrategy.COMBINED:
        return _connect_ports(
            composition_path, strategy, source_expression, target_expression,
            single_connections_only, allow_closed_loop_connections, interface_paths,
        )

    return _connect_ports_combined(
        composition_path, selected_interface, source_expression, target_expression,
        single_connections_only, allow_closed_loop_connections, interface_paths,
    )


def _connect_ports_combined(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    composition_path, selected_interface, source_expression, target_expression,
    single_connections_only, allow_closed_loop_connections, interface_paths,
):
    passes = []
    connections = []
    for pass_strategy, source, target in (
        (_PortConnectionStrategy.PORT_NAME, source_expression, target_expression),
        (selected_interface, None, None),
    ):
        result = _connect_ports(
            composition_path, pass_strategy, source, target,
            single_connections_only, allow_closed_loop_connections,
            interface_paths if pass_strategy == _PortConnectionStrategy.INTERFACE_TYPE else None,
        )
        passes.append({"strategy": pass_strategy, **result})
        if result["status"] == "error":
            return {**result, "path": composition_path, "connection_count": len(connections),
                    "connections": connections, "passes": passes}
        connections.extend(result["connections"])
    return success_response({
        "path": result["path"], "connection_count": len(connections),
        "connections": connections, "passes": passes,
    })


def _connect_ports(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    composition_path, strategy, source_expression, target_expression,
    single_connections_only, allow_closed_loop_connections, interface_paths,
):
    matching_started = False
    try:
        composition = resolve_component(get_active_project(), composition_path)
        if composition.AutosarType != "COMPOSITION-SW-COMPONENT-TYPE":
            raise PermanentError(
                f"Element at '{composition_path}' is not a composition.",
                error_code=ErrorCode.INVALID_AUTOSAR_ELEMENT_TYPE,
            )
        configuration = composition.CreatePortConnectorConfiguration()
        configuration.IsPortNameStrategyChecked = strategy == _PortConnectionStrategy.PORT_NAME
        configuration.IsInterfaceNameStrategyChecked = strategy == _PortConnectionStrategy.INTERFACE_NAME
        configuration.IsInterfaceTypeStrategyChecked = strategy == _PortConnectionStrategy.INTERFACE_TYPE
        configuration.SourceExpression = source_expression or ""
        configuration.TargetExpression = target_expression or ""
        configuration.IsSingleConnectionsChecked = single_connections_only
        configuration.IsUseAllInterfacesChecked = interface_paths is None
        configuration.IsUseSelectedInterfacesChecked = interface_paths is not None
        if interface_paths is not None:
            configuration.SetInterfaceCollection(interface_paths)
        configuration.IsCreateDelegationPortsChecked = False
        configuration.IsCreateSingleDelegationPortChecked = False
        configuration.IsClosedLoopConnectionsChecked = allow_closed_loop_connections
        configuration.IsConnectPortsWithoutInterfaceChecked = False

        matching_started = True
        result = composition.ConnectPortsOfComposition(configuration)
        if result is None:
            raise RuntimeError("SystemDesk returned no port connector result.")
        connections = []
        for connector in result.Connections:
            connections.append({
                "path": connector.AUTOSARPathName,
                "type": connector.AutosarType,
                "provider": {
                    "component": connector.LeftComponentPrototype.ShortName
                    if connector.LeftComponentPrototype is not None else None,
                    "port": connector.LeftPortPrototype.ShortName,
                },
                "requester": {
                    "component": connector.RightComponentPrototype.ShortName
                    if connector.RightComponentPrototype is not None else None,
                    "port": connector.RightPortPrototype.ShortName,
                },
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


def _check_interface_paths(project, interface_paths):
    for path in interface_paths:
        matches = project.GetElementsByARPath(path)
        if not matches:
            raise PermanentError(
                f"No interface found at '{path}'.", error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
            )
        if len(matches) != 1:
            raise PermanentError(
                f"Interface path '{path}' identifies multiple elements.",
                error_code=ErrorCode.AMBIGUOUS_AUTOSAR_PATH,
            )
        if matches[0].AutosarType not in {
            "SENDER-RECEIVER-INTERFACE", "CLIENT-SERVER-INTERFACE", "PARAMETER-INTERFACE",
            "MODE-SWITCH-INTERFACE", "TRIGGER-INTERFACE", "NV-DATA-INTERFACE",
        }:
            raise PermanentError(
                f"Element at '{path}' is not a port interface.",
                error_code=ErrorCode.INVALID_AUTOSAR_ELEMENT_TYPE,
            )


def _check_port_connection_arguments(strategy, source_expression, target_expression):
    if strategy == _PortConnectionStrategy.PORT_NAME:
        valid = source_expression is not None and target_expression is not None
    else:
        valid = source_expression is None and target_expression is None
    if not valid:
        raise PermanentError(
            "port_name requires source_expression and target_expression; "
            "interface_name and interface_type accept neither.",
            error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
        )
