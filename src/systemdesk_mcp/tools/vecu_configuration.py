"""V-ECU configuration tools: inspect the configuration, generate the runnable mapping, configure RTE interventions."""

from typing import Annotated, Any, Literal, NamedTuple, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from systemdesk_mcp.common import (
    AutosarShortName,
    ErrorCode,
    PermanentError,
    TimeoutSeconds,
    VecuNameParam,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_vecu, logged, on_com_thread

Access = Literal["dap", "rte_service_port"]

# SystemDesk enum values (dSPACE.SystemDesk.Components.AutomationFacade.RteInterventions).
_KIND_OPTION_READ_AND_WRITE_ONLY = 3  # InterventionPointKindOption
_POINT_KIND_READ = 0  # InterventionPointKind
_SERVICE_TYPE_BY_ACCESS = {"dap": 1, "rte_service_port": 2}  # InterventionServiceType
_ACCESS_BY_SERVICE_TYPE = {value: key for key, value in _SERVICE_TYPE_BY_ACCESS.items()}  # InterventionServiceType

_SERVICE_COMPONENT_TYPE = "SERVICE-SW-COMPONENT-TYPE"
# Only atomic SW component types have internal behaviors.
_COMPONENT_TYPES_WITHOUT_INTERNAL_BEHAVIORS = ("COMPOSITION-SW-COMPONENT-TYPE", "PARAMETER-SW-COMPONENT-TYPE")


class InterventionPointSpec(BaseModel):
    """A port data element for which RTE intervention points are created."""

    model_config = ConfigDict(extra="forbid")

    component: AutosarShortName = Field(description="SW component prototype. Example: 'FuelsysController'")
    port: AutosarShortName = Field(description="Port of the component. Example: 'RpCorrectedSensors'")
    data_element: AutosarShortName = Field(description="Data element of the port interface. Example: 'Sensors'")


# --------------------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------------------

def _get_module_configuration(ecu_configuration, definition_name: str, required: bool = True):
    module = ecu_configuration.GetModuleConfigurationByDefinitionName(definition_name, False)
    if module is None and required:
        raise PermanentError(
            f"The ECU configuration '{ecu_configuration.Name}' has no {definition_name} module configuration.",
            error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
        )
    return module


def _short_name(element) -> Optional[str]:
    return None if element is None else element.ShortName


def _check_unique(values: list[Any], what: str) -> None:
    duplicates = sorted({str(v) for v in values if values.count(v) > 1})
    if duplicates:
        raise PermanentError(
            f"Each {what} may only be given once. Duplicates: {', '.join(duplicates)}.",
            error_code=ErrorCode.INVALID_ARGUMENTS,
        )


def _flat_view_components(ecu_configuration) -> list[Any]:
    return list(ecu_configuration.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements)


def _rte_events(component) -> list[Any]:
    component_type = component.TypeTref
    if component_type.AutosarType in _COMPONENT_TYPES_WITHOUT_INTERNAL_BEHAVIORS:
        return []
    events = []
    for behavior in component_type.InternalBehaviors.Elements:
        events.extend(behavior.Events.Elements)
    return events


def _period_ms(seconds: float) -> float:
    return round(seconds * 1000, 6)


def _task_period_ms(task) -> Optional[float]:
    if not task.IsOsTaskPeriodDefined:
        return None
    return _period_ms(task.OsTaskPeriod)


def _event_mapping(rte_configuration, component_name: str, event_name: str):
    if rte_configuration is None:
        return None
    instance = rte_configuration.RteSwComponentInstances.Item(component_name)
    if instance is None:
        return None
    return instance.RteEventToTaskMappings.Item(event_name)


class InterventionPointId(NamedTuple):
    """Identifies a port data element intervention point."""

    component: str  # short name of the SW component prototype
    port: str  # short name of the port prototype
    data_element: str  # short name of the data element
    kind: int  # InterventionPointKind (read or write)


def _intervention_point_id(point) -> Optional[InterventionPointId]:
    """Return the InterventionPointId of a port data element intervention point, else None.

    Points of inter-runnable variables (no port) and of service components (e.g. Rte, EcuM) are skipped.
    """
    try:
        component = point.ContextSwComponentPrototype
        variable = point.ElementRef.AccessedVariable.AutosarVariableIref
        port = variable.PortPrototypeRef
        data_element = variable.TargetDataPrototypeRef
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    if component is None or port is None or data_element is None:
        return None
    if component.TypeTref.AutosarType == _SERVICE_COMPONENT_TYPE:
        return None
    return InterventionPointId(component.ShortName, port.ShortName, data_element.ShortName, point.Kind)


def _access_of(point, service) -> Optional[str]:
    if service is None:
        return None
    service_type = service.InputType if point.Kind == _POINT_KIND_READ else service.OutputType
    return _ACCESS_BY_SERVICE_TYPE.get(service_type)


# --------------------------------------------------------------------------------------------------
# get_vecu_configuration
# --------------------------------------------------------------------------------------------------

def _read_runnables(ecu_configuration) -> list[dict[str, Any]]:
    rte_configuration = _get_module_configuration(ecu_configuration, "Rte", required=False)
    entries = []
    for component in _flat_view_components(ecu_configuration):
        for event in _rte_events(component):
            mapping = _event_mapping(rte_configuration, component.ShortName, event.ShortName)
            task = mapping.RteMappedToTaskRef if mapping is not None else None
            entries.append({
                "component": component.ShortName,
                "runnable": _short_name(event.StartOnEventRef),
                "rte_event": event.ShortName,
                "event_type": event.AutosarType,
                "period_ms": _period_ms(event.Period) if event.AutosarType == "TIMING-EVENT" else None,
                "mapped_task": _short_name(task),
                "position": mapping.RtePositionInTask if task is not None else None,
            })
    return entries


def _read_os_tasks(ecu_configuration) -> list[dict[str, Any]]:
    os_configuration = _get_module_configuration(ecu_configuration, "Os", required=False)
    if os_configuration is None:
        return []
    return [
        {"name": task.ShortName, "period_ms": _task_period_ms(task), "priority": task.OsTaskPriority}
        for task in os_configuration.OsTasks.Elements
    ]


def _read_possible_rte_intervention_points(ecu_configuration) -> list[dict[str, Any]]:
    possible = ecu_configuration.RteInterventions.GetPossibleInterventionPoints(_KIND_OPTION_READ_AND_WRITE_ONLY)
    keys = []
    for point in possible:
        key = _intervention_point_id(point)
        if key is not None and key[:3] not in keys:
            keys.append(key[:3])
    return [{"component": c, "port": p, "data_element": d} for c, p, d in keys]


def _read_existing_rte_intervention_points(ecu_configuration) -> list[dict[str, Any]]:
    entries = []
    for point in ecu_configuration.RteInterventions.GetInterventionPoints():
        service = point.GetInterventionService()
        entries.append({
            "point": point.ShortName,
            "service": _short_name(service),
            "access": _access_of(point, service),
        })
    return entries


def _reject_duplicates(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("Each list may only be given once.")
    return values


@mcp.tool(
    title="Get V-ECU Configuration",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@logged
@on_com_thread
def get_vecu_configuration(
    vecu_name: VecuNameParam = None,
    include: Annotated[Optional[Annotated[list[Literal[
        "runnables", "os_tasks", "possible_rte_intervention_points", "existing_rte_intervention_points",
    ]], AfterValidator(_reject_duplicates), Field(json_schema_extra={"uniqueItems": True})]], Field(
        min_length=1,
        description=(
            "Lists to return, at least one, each only once: 'runnables' (runnables with their RTE events and "
            "task mapping), 'os_tasks' (OS tasks), 'possible_rte_intervention_points' (port data elements where "
            "RTE interventions can still be added), 'existing_rte_intervention_points' (existing RTE intervention "
            "points). Omit (null) to return all four lists. Example: [\"runnables\", \"os_tasks\"]"
        ),
    )] = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the configuration to be read. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Return the current runnable mapping and RTE intervention configuration of a V-ECU.

    Use it to see what exists before changing anything and to get the names that
    configure_rte_interventions needs. Does not modify the project.

    Returned lists (only those given in include; all four if include is omitted):
    - runnables: list of {component, runnable, rte_event, event_type, period_ms, mapped_task, position}.
      period_ms is set for timing events only. mapped_task and position are null if the event is not
      mapped yet; position is the execution order inside the task.
    - os_tasks: list of {name, period_ms, priority}; period_ms is null for non-cyclic tasks.
    - possible_rte_intervention_points: port data elements of SW components (not of service components such as
      Rte or EcuM) where RTE interventions can still be added, as list of {component, port, data_element}.
    - existing_rte_intervention_points: existing intervention points as list of {point, service, access};
      access is 'dap', 'rte_service_port' or null if the point is not mapped to a service.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open.
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name and the included lists.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - transient / TIMEOUT: reading the configuration timed out
    - permanent / UNEXPECTED_ERROR: reading the configuration failed
    """
    try:
        v_ecu = get_vecu(vecu_name)
        ecu_configuration = v_ecu.EcuConfiguration
        result: dict[str, Any] = {"vecu_name": v_ecu.Name}
        if include is None or "runnables" in include:
            result["runnables"] = _read_runnables(ecu_configuration)
        if include is None or "os_tasks" in include:
            result["os_tasks"] = _read_os_tasks(ecu_configuration)
        if include is None or "possible_rte_intervention_points" in include:
            result["possible_rte_intervention_points"] = _read_possible_rte_intervention_points(ecu_configuration)
        if include is None or "existing_rte_intervention_points" in include:
            result["existing_rte_intervention_points"] = _read_existing_rte_intervention_points(ecu_configuration)
        return success_response(result)

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to read the V-ECU configuration.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


# --------------------------------------------------------------------------------------------------
# generate_runnable_mapping
# --------------------------------------------------------------------------------------------------

@mcp.tool(
    title="Generate Runnable Mapping",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@logged
@on_com_thread
def generate_runnable_mapping(
    vecu_name: VecuNameParam = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the runnable mapping generation to complete. Example: 240",
    )] = 240,
) -> dict[str, Any]:
    """
    Regenerate the runnable mapping (runnable schedule) of a V-ECU with SystemDesk.

    Deletes the existing runnable mapping and all tasks of the runnable mapping, then lets SystemDesk
    generate a new mapping: it maps the RTE events of runnables to
    OS tasks, creates the OS tasks and sets the execution order. Generation follows the event mapping
    options stored in the ECU configuration (e.g. combine tasks, rate monotonic scheduling); this tool
    does not change them. Use it after the SW components of an existing V-ECU have changed, or for a
    V-ECU created with create_runnable_mapping=false. Not needed directly after get_or_create_vecu
    created a V-ECU with create_runnable_mapping=true (the default), since that already generated the
    same mapping. Repeating the call regenerates the same mapping. Changes are not saved; use save_project.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open.
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name and mapping: the mapped RTE events as list of {component, runnable, rte_event,
    event_type, period_ms, mapped_task, position}, in the same format and order as runnables of
    get_vecu_configuration.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - permanent / SYSTEMDESK_OPERATION_FAILED: SystemDesk could not clear the existing runnable mapping
      (nothing is generated), or could not generate the new mapping (the existing mapping is already
      deleted; the V-ECU then has no runnable mapping)
    - transient / TIMEOUT: runnable mapping generation timed out
    - permanent / UNEXPECTED_ERROR: a SystemDesk call failed
    """
    try:
        v_ecu = get_vecu(vecu_name)
        ecu_configuration = v_ecu.EcuConfiguration

        if not ecu_configuration.ClearRunnableSchedule():
            raise PermanentError(
                f"SystemDesk could not clear the runnable mapping of V-ECU '{v_ecu.Name}'.",
                error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
            )
        if not ecu_configuration.GenerateRunnableSchedule():
            raise PermanentError(
                f"SystemDesk could not generate the runnable mapping of V-ECU '{v_ecu.Name}'. "
                "The existing mapping was already deleted.",
                error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
            )

        mapping = [entry for entry in _read_runnables(ecu_configuration) if entry["mapped_task"] is not None]
        return success_response({"vecu_name": v_ecu.Name, "mapping": mapping})

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to generate the runnable mapping.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


# --------------------------------------------------------------------------------------------------
# configure_rte_interventions
# --------------------------------------------------------------------------------------------------


def _unique_service_name(key: InterventionPointId, existing_names: set[str]) -> str:
    """Return the service name of key, with the lowest numeric suffix (1, 2, ...) not in existing_names."""
    direction = "Input" if key.kind == _POINT_KIND_READ else "Output"
    base_name = f"{key.component}_{key.port}_{direction}Service"
    name = base_name
    suffix = 0
    while name in existing_names:
        suffix += 1
        name = f"{base_name}{suffix}"
    return name


def _collect_intervention_points(rte_interventions) -> tuple[dict[InterventionPointId, list[Any]], dict[InterventionPointId, list[Any]], set]:
    """Return possible points and unmapped existing points by key, and the targets of existing points."""
    possible: dict[InterventionPointId, list[Any]] = {}
    for point in rte_interventions.GetPossibleInterventionPoints(_KIND_OPTION_READ_AND_WRITE_ONLY):
        key = _intervention_point_id(point)
        if key is not None:
            possible.setdefault(key, []).append(point)
    unmapped: dict[InterventionPointId, list[Any]] = {}
    existing_targets = set()
    for point in rte_interventions.GetInterventionPoints():
        key = _intervention_point_id(point)
        if key is None:
            continue
        existing_targets.add(key[:3])
        if point.GetInterventionService() is None:
            unmapped.setdefault(key, []).append(point)
    return possible, unmapped, existing_targets


def _select_intervention_points(intervention_points, possible, unmapped, existing_targets) -> set:
    if intervention_points is None:
        return set(possible) | set(unmapped)
    requested = [(p.component, p.port, p.data_element) for p in intervention_points]
    _check_unique(requested, "(component, port, data_element)")
    known = {key[:3] for key in possible} | existing_targets
    missing = [r for r in requested if r not in known]
    if missing:
        raise PermanentError(
            "No RTE intervention point exists or is possible for: "
            + ", ".join("/".join(m) for m in missing)
            + ". Call get_vecu_configuration to list the possible points.",
            error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
        )
    return {key for key in set(possible) | set(unmapped) if key[:3] in requested}


def _map_points_to_service(rte_interventions, key: InterventionPointId, points: list[Any], access: str) -> list[str]:
    """Map each point to its own newly created service of key; return the names of the created services."""
    existing_names = {service.ShortName for service in rte_interventions.InterventionServices.Elements}
    created: list[str] = []
    for point in points:
        name = _unique_service_name(key, existing_names)
        service = rte_interventions.InterventionServices.AddNew(name)
        if key.kind == _POINT_KIND_READ:
            service.InputType = _SERVICE_TYPE_BY_ACCESS[access]
        else:
            service.OutputType = _SERVICE_TYPE_BY_ACCESS[access]
        service.AddInterventionPoints([point])
        existing_names.add(service.ShortName)
        created.append(service.ShortName)
    return created


@mcp.tool(
    title="Configure RTE Interventions",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@logged
@on_com_thread
def configure_rte_interventions(  # pylint: disable=too-many-locals
    vecu_name: VecuNameParam = None,
    intervention_points: Annotated[Optional[Annotated[
        list[InterventionPointSpec], Field(json_schema_extra={"uniqueItems": True}),
    ]], Field(
        description=(
            "Port data elements to create RTE intervention points for. Omitted or null: all possible points, "
            "as listed by get_vecu_configuration (possible_rte_intervention_points). Empty list []: no points or "
            "services are created, e.g. to only set enable. "
            "Example: [{\"component\": \"FuelsysController\", \"port\": \"RpCorrectedSensors\", "
            "\"data_element\": \"Sensors\"}]"
        ),
    )] = None,
    access: Annotated[Access, Field(
        description=(
            "How the signals of the intervention services are accessed: 'dap' (data access points, "
            "from outside the V-ECU) or 'rte_service_port' (RTE service ports, inside the V-ECU). Example: 'dap'"
        ),
    )] = "dap",
    enable: Annotated[bool, Field(
        description=(
            "Value to set for the V-ECU's RTE intervention code generation switch. Independent of "
            "intervention_points: points are created as selected by intervention_points also when enable is "
            "false, and existing points and services are never removed. To only switch code generation off, "
            "pass enable=false with intervention_points=[]. Example: true"
        ),
    )] = True,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the configuration to complete. Example: 240",
    )] = 240,
) -> dict[str, Any]:
    """
    Create RTE intervention points and services for port data elements and set the RTE intervention
    code generation switch.

    With RTE interventions, the simulation can overwrite the communication of SW components,
    e.g. to stimulate an input or to inject an error. Only port data elements of SW components are
    supported (no inter-runnable variables, no service components such as Rte or EcuM). For each
    (component, port, data_element) and direction, all its possible intervention points (one per
    runnable access) are created, and each point is mapped to its own new intervention service named
    <component>_<port>_<Input|Output>Service. If that name is taken, the lowest free numeric suffix
    is appended (e.g. ...InputService1, ...InputService2). Existing points without a service are
    mapped the same way.

    Existing intervention points and services are kept; already existing points are not created
    again, so repeating the call with the same arguments creates nothing new. The access of existing
    intervention services is never changed: access only applies to newly created points and to
    existing points without a service.
    Sets the V-ECU's RTE intervention code generation switch to enable (default true).
    Changes are not saved; use save_project.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open.
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name, points_created and services_created (names), access, and enabled.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - permanent / INVALID_ARGUMENTS: an intervention point is given twice
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: no intervention point exists or is possible for a
      given component, port and data_element
    - transient / TIMEOUT: the configuration timed out
    - permanent / UNEXPECTED_ERROR: a SystemDesk call failed
    """
    try:
        v_ecu = get_vecu(vecu_name)
        rte_interventions = v_ecu.EcuConfiguration.RteInterventions

        points_created: list[str] = []
        services_created: list[str] = []
        try:
            # Delayed mode batches the changes, which greatly reduces the runtime for many points and services.
            rte_interventions.EnterDelayedMode()
            # An empty list selects no points, so skip reading all intervention points from SystemDesk.
            if intervention_points != []:
                possible, unmapped, existing_targets = _collect_intervention_points(rte_interventions)
                selected = _select_intervention_points(intervention_points, possible, unmapped, existing_targets)

                for key in sorted(selected):
                    points = list(unmapped.get(key, []))
                    if key in possible:
                        created = list(rte_interventions.CreateInterventionPoints(possible[key]))
                        points.extend(created)
                        points_created.extend(p.ShortName for p in created)
                    if points:
                        services_created.extend(_map_points_to_service(rte_interventions, key, points, access))

            rte_interventions.InterventionEnableCodeGeneration = enable
        finally:
            rte_interventions.ExitDelayedMode()

        return success_response({
            "vecu_name": v_ecu.Name,
            "points_created": points_created,
            "services_created": services_created,
            "access": access,
            "enabled": bool(rte_interventions.InterventionEnableCodeGeneration),
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to configure the RTE interventions.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )
