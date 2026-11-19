"""AUTOSAR composition creation and port connections."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


MatchingStrategy = Literal["common_name_pattern", "common_interface_names", "common_interface_types"]


# Chosen limit, not a documented SystemDesk one.
_MAX_EXPRESSION_LENGTH = 256


class CommonNamePatternOptions(BaseModel):
    """SystemDesk common name patterns for provided and required ports."""

    model_config = ConfigDict(extra="forbid")

    pport_expression: str = Field(
        min_length=1, max_length=_MAX_EXPRESSION_LENGTH,
        description="Provided-port pattern. Macros: <SWC>, <Interface>, <equal>. Example: 'P_<equal>'",
    )
    rport_expression: str = Field(
        min_length=1, max_length=_MAX_EXPRESSION_LENGTH,
        description="Required-port pattern. Macros: <SWC>, <Interface>, <equal>. Example: 'R_<equal>'",
    )


class ConnectionOptions(BaseModel):
    """Connection selection and closed-loop matching options."""

    model_config = ConfigDict(extra="forbid")

    single_connections_only: bool = Field(
        default=True,
        description=(
            "True restricts matching to ports with exactly one possible connection. "
            "False includes single and multiple connections, subject to SystemDesk rules. Example: False"
        ),
    )
    allow_closed_loop_connections: bool = Field(
        default=False,
        description="Allow closed-loop connections during matching. Example: True",
    )


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
def connect_composition_ports(  # pylint: disable=dangerous-default-value,too-many-arguments,too-many-positional-arguments
    composition_path: Annotated[AutosarElementPath, Field(
        description="Absolute AUTOSAR path of an existing composition. Example: '/SWA/Top'",
    )],
    matching_strategies: Annotated[list[MatchingStrategy], Field(
        min_length=1,
        description=(
            "Nonempty selection of matching strategies; order has no significance and duplicates are rejected. "
            "All selected flags are enabled in one configuration and one matching call. "
            'Example: ["common_name_pattern", "common_interface_types"]'
        ),
    )],
    common_name_pattern_options: Annotated[CommonNamePatternOptions | None, Field(
        description=(
            "Required only with common_name_pattern; otherwise omit. "
            'Example: {"pport_expression": "P_<equal>", "rport_expression": "R_<equal>"}'
        ),
    )] = None,
    interface_paths: Annotated[list[AutosarElementPath], Field(
        description=(
            "Empty list means all interfaces. Nonempty lists require common_interface_types. "
            'Example: ["/Interfaces/Speed"]'
        ),
    )] = [],
    connection_options: Annotated[ConnectionOptions | None, Field(
        description=(
            "Omit to use single_connections_only=True and allow_closed_loop_connections=False. "
            'Example: {"single_connections_only": false, "allow_closed_loop_connections": false}'
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

    matching_strategies is a required nonempty selection of common_name_pattern,
    common_interface_names, and common_interface_types. Order has no significance;
    duplicates are rejected. All selected flags are enabled together in one
    configuration and one ConnectPortsOfComposition call, not sequential passes.
    SystemDesk requires ports to fulfill all selected filters to qualify for connection.

    connection_options.single_connections_only=False includes single and multiple connections,
    subject to SystemDesk rules. When both are required, call once with False;
    no preceding call with True is needed. The default True restricts matching
    to ports with exactly one possible connection.

    common_name_pattern requires common_name_pattern_options with pport_expression
    and rport_expression; otherwise omit that object. Nonempty interface_paths
    requires common_interface_types; an empty list means all interfaces and is
    accepted with every selection. All arguments and interface paths are checked
    before configuring connections. Both nested objects reject unknown fields.
    Omit connection_options to use its defaults.
    Expressions are SystemDesk common name patterns (regular expressions), with
    <SWC> as software component name, <Interface> as interface name, and <equal> which specifies
    a part that has to be identical in both port names. Syntax is not
    validated: malformed or non-matching patterns can succeed with zero connections.
    New port creation and connections without interfaces are disabled.
    Returns path, connection_count and connections, alongside status. Each connection
    includes path, type, provider and requester. Endpoints contain component and port
    prototype names; component is null for a composition boundary endpoint.
    The timeout covers the entire operation; timed-out work continues on the COM worker.
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
    try:
        project = get_active_project()
        _check_port_connection_arguments(matching_strategies, common_name_pattern_options, interface_paths)
        if interface_paths:
            _check_interface_paths(project, interface_paths)
    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)

    return _connect_ports(
        composition_path, matching_strategies, common_name_pattern_options,
        interface_paths, connection_options or ConnectionOptions(),
    )


def _connect_ports(
    composition_path, matching_strategies, common_name_pattern_options,
    interface_paths, connection_options,
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
        configuration.IsPortNameStrategyChecked = "common_name_pattern" in matching_strategies
        configuration.IsInterfaceNameStrategyChecked = "common_interface_names" in matching_strategies
        configuration.IsInterfaceTypeStrategyChecked = "common_interface_types" in matching_strategies
        configuration.SourceExpression = common_name_pattern_options.pport_expression if common_name_pattern_options else ""
        configuration.TargetExpression = common_name_pattern_options.rport_expression if common_name_pattern_options else ""
        configuration.IsSingleConnectionsChecked = connection_options.single_connections_only
        configuration.IsUseAllInterfacesChecked = not interface_paths
        configuration.IsUseSelectedInterfacesChecked = bool(interface_paths)
        if interface_paths:
            configuration.SetInterfaceCollection(interface_paths)
        configuration.IsCreateDelegationPortsChecked = False
        configuration.IsCreateSingleDelegationPortChecked = False
        configuration.IsClosedLoopConnectionsChecked = connection_options.allow_closed_loop_connections
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


def _check_port_connection_arguments(matching_strategies, common_name_pattern_options, interface_paths):
    if not matching_strategies or any(strategy not in {
        "common_name_pattern", "common_interface_names", "common_interface_types",
    } for strategy in matching_strategies) or len(set(matching_strategies)) != len(matching_strategies):
        raise PermanentError(
            "matching_strategies must be a nonempty selection of supported strategies without duplicates.",
            error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
        )
    if ("common_name_pattern" in matching_strategies) != (common_name_pattern_options is not None):
        raise PermanentError(
            "common_name_pattern requires common_name_pattern_options; otherwise omit that object.",
            error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
        )
    if interface_paths and "common_interface_types" not in matching_strategies:
        raise PermanentError(
            "Nonempty interface_paths requires common_interface_types matching.",
            error_code=ErrorCode.INVALID_PORT_CONNECTOR_CONFIGURATION,
        )
