"""V-ECU configuration tools: inspect the configuration, map runnables to OS tasks, configure RTE interventions."""

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
from systemdesk_mcp.runtime import get_app, get_vecu, logged, on_com_thread

Section = Literal["runnables", "os_tasks", "rte_intervention_points", "rte_interventions"]
ALL_SECTIONS: tuple[Section, ...] = ("runnables", "os_tasks", "rte_intervention_points", "rte_interventions")
Access = Literal["dap", "rte_service_port"]

# SystemDesk enum values (dSPACE.SystemDesk.Components.AutomationFacade.RteInterventions).
_KIND_OPTION_READ_AND_WRITE_ONLY = 3
_POINT_KIND_READ = 0
_SERVICE_TYPE_BY_ACCESS = {"dap": 1, "rte_service_port": 2}
_ACCESS_BY_SERVICE_TYPE = {value: key for key, value in _SERVICE_TYPE_BY_ACCESS.items()}

_UPDATE_RTE_CONFIGURATION = "Update Rte Configuration"
_SERVICE_COMPONENT_TYPE = "SERVICE-SW-COMPONENT-TYPE"
_MIN_PERIOD_MS = 0.0001
_MAX_PERIOD_MS = 3_600_000
_MAX_PRIORITY = 2**31 - 1


class OsTaskSpec(BaseModel):
    """An OS task to create or update."""

    model_config = ConfigDict(extra="forbid")

    name: AutosarShortName = Field(description="Name of the OS task. Example: 'Task10ms'")
    period_ms: Optional[float] = Field(
        default=None, ge=_MIN_PERIOD_MS, le=_MAX_PERIOD_MS,
        description=(
            "Activation period of the task in milliseconds. Omit (null) for non-cyclic tasks, "
            "e.g. a task for init events. Example: 10"
        ),
    )
    priority: int = Field(
        ge=0, le=_MAX_PRIORITY,
        description="OS task priority; 0 is the lowest priority. Example: 10",
    )


class RunnableMappingSpec(BaseModel):
    """A mapping of the RTE event of a runnable to an OS task."""

    model_config = ConfigDict(extra="forbid")

    task: AutosarShortName = Field(description="Name of the OS task to map to. Example: 'Task10ms'")
    component: AutosarShortName = Field(
        description="SW component prototype in the V-ECU, as listed by get_vecu_configuration. Example: 'FuelsysController'",
    )
    runnable: AutosarShortName = Field(description="Runnable entity started by the event. Example: 'AirflowCalculation'")
    rte_event: AutosarShortName = Field(description="RTE event that triggers the runnable. Example: 'AirflowCalculation10ms'")
    position: Optional[int] = Field(
        default=None, ge=0,
        description=(
            "Execution order inside the task (0 runs first). Omit (null) to keep the current position "
            "if the event is already mapped to this task, otherwise to append it. Example: 0"
        ),
    )


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


def _flat_view_components(ecu_configuration) -> list[Any]:
    return list(ecu_configuration.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements)


def _rte_events(component) -> list[Any]:
    internal_behaviors = getattr(component.TypeTref, "InternalBehaviors", None)
    if internal_behaviors is None:
        return []
    events = []
    for behavior in internal_behaviors.Elements:
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


def _point_key(point) -> Optional[tuple[str, str, str, int]]:
    """Return (component, port, data_element, kind) of a port data element intervention point, else None.

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
    return component.ShortName, port.ShortName, data_element.ShortName, point.Kind


def _access_of(point, service) -> Optional[str]:
    if service is None:
        return None
    service_type = service.InputType if point.Kind == _POINT_KIND_READ else service.OutputType
    return _ACCESS_BY_SERVICE_TYPE.get(service_type)


# --------------------------------------------------------------------------------------------------
# get_vecu_configuration
# --------------------------------------------------------------------------------------------------

def _runnables_section(ecu_configuration) -> list[dict[str, Any]]:
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


def _os_tasks_section(ecu_configuration) -> list[dict[str, Any]]:
    os_configuration = _get_module_configuration(ecu_configuration, "Os", required=False)
    if os_configuration is None:
        return []
    return [
        {"name": task.ShortName, "period_ms": _task_period_ms(task), "priority": task.OsTaskPriority}
        for task in os_configuration.OsTasks.Elements
    ]


def _intervention_points_section(ecu_configuration) -> list[dict[str, Any]]:
    possible = ecu_configuration.RteInterventions.GetPossibleInterventionPoints(_KIND_OPTION_READ_AND_WRITE_ONLY)
    keys = []
    for point in possible:
        key = _point_key(point)
        if key is not None and key[:3] not in keys:
            keys.append(key[:3])
    return [{"component": c, "port": p, "data_element": d} for c, p, d in keys]


def _interventions_section(ecu_configuration) -> list[dict[str, Any]]:
    entries = []
    for point in ecu_configuration.RteInterventions.GetInterventionPoints():
        service = point.GetInterventionService()
        entries.append({
            "point": point.ShortName,
            "service": _short_name(service),
            "access": _access_of(point, service),
        })
    return entries


_SECTION_READERS = {
    "runnables": _runnables_section,
    "os_tasks": _os_tasks_section,
    "rte_intervention_points": _intervention_points_section,
    "rte_interventions": _interventions_section,
}


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
    sections: Annotated[Optional[list[Section]], Field(
        min_length=1,
        description=(
            "Parts of the configuration to return: 'runnables', 'os_tasks', 'rte_intervention_points', "
            "'rte_interventions'. Defaults to all. Example: ['runnables', 'os_tasks']"
        ),
    )] = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the configuration to be read. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Return the current runnable mapping and RTE intervention configuration of a V-ECU.

    Use it to see what exists before changing anything and to get the names that
    configure_runnable_mapping and configure_rte_interventions need. Does not modify the project.

    Sections (one entry per requested section):
    - runnables: list of {component, runnable, rte_event, event_type, period_ms, mapped_task, position}.
      period_ms is set for timing events only. mapped_task and position are null if the event is not
      mapped yet; position is the execution order inside the task. Timing and init events must be mapped.
    - os_tasks: list of {name, period_ms, priority}; period_ms is null for non-cyclic tasks.
    - rte_intervention_points: port data elements of SW components (not of service components such as
      Rte or EcuM) where RTE interventions can still be added, as list of {component, port, data_element}.
    - rte_interventions: existing intervention points as list of {point, service, access};
      access is 'dap', 'rte_service_port' or null if the point is not mapped to a service.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open.
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name and one entry per requested section.

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
        requested = [s for s in ALL_SECTIONS if sections is None or s in sections]
        result: dict[str, Any] = {"vecu_name": v_ecu.Name}
        for section in requested:
            result[section] = _SECTION_READERS[section](ecu_configuration)
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
# configure_runnable_mapping
# --------------------------------------------------------------------------------------------------

def _check_unique(values: list[Any], what: str) -> None:
    duplicates = sorted({str(v) for v in values if values.count(v) > 1})
    if duplicates:
        raise PermanentError(
            f"Each {what} may only be given once. Duplicates: {', '.join(duplicates)}.",
            error_code=ErrorCode.INVALID_ARGUMENTS,
        )


def _resolve_event(components: dict[str, Any], spec: RunnableMappingSpec):
    component = components.get(spec.component)
    if component is None:
        raise PermanentError(
            f"The V-ECU has no SW component '{spec.component}'. "
            f"Available components: {', '.join(components) or 'none'}.",
            error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
        )
    for event in _rte_events(component):
        if event.ShortName == spec.rte_event:
            runnable = _short_name(event.StartOnEventRef)
            if runnable != spec.runnable:
                raise PermanentError(
                    f"RTE event '{spec.rte_event}' of component '{spec.component}' starts runnable "
                    f"'{runnable}', not '{spec.runnable}'.",
                    error_code=ErrorCode.INVALID_ARGUMENTS,
                )
            return event
    raise PermanentError(
        f"Component '{spec.component}' has no RTE event '{spec.rte_event}'.",
        error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
    )


def _resolve_mapping_request(ecu_configuration, os_configuration, tasks, mappings) -> list[Any]:
    """Validate the request against the model before anything is changed; return the RTE events."""
    _check_unique([t.name for t in tasks], "task name")
    _check_unique([(m.component, m.rte_event) for m in mappings], "(component, rte_event) pair")
    components = {c.ShortName: c for c in _flat_view_components(ecu_configuration)}
    events = [_resolve_event(components, m) for m in mappings]
    requested_tasks = {t.name for t in tasks}
    for spec in mappings:
        if spec.task not in requested_tasks and os_configuration.OsTasks.Item(spec.task) is None:
            raise PermanentError(
                f"OS task '{spec.task}' does not exist and is not given in tasks.",
                error_code=ErrorCode.AUTOSAR_ELEMENT_NOT_FOUND,
            )
    return events


def _create_or_update_task(os_configuration, spec: OsTaskSpec) -> None:
    task = os_configuration.OsTasks.Item(spec.name)
    if task is None:
        task = os_configuration.OsTasks.Add(spec.name)
        task.OsTaskActivation = 1
    task.OsTaskPriority = spec.priority
    if spec.period_ms is not None:
        task.OsTaskPeriod = spec.period_ms / 1000


def _map_event(os_configuration, rte_configuration, spec: RunnableMappingSpec, event) -> dict[str, Any]:
    instance = rte_configuration.RteSwComponentInstances.Item(spec.component)
    mapping = instance.RteEventToTaskMappings.Item(spec.rte_event)
    if spec.position is not None:
        position = spec.position
    elif mapping is not None and _short_name(mapping.RteMappedToTaskRef) == spec.task:
        position = mapping.RtePositionInTask
    else:
        position = _next_position_in_task(rte_configuration, spec.task)
    if mapping is None:
        mapping = instance.RteEventToTaskMappings.Add(spec.rte_event)
    mapping.RteEventRef = event
    mapping.RteMappedToTaskRef = os_configuration.OsTasks.Item(spec.task)
    mapping.RtePositionInTask = position
    return {
        "task": spec.task, "component": spec.component, "runnable": spec.runnable,
        "rte_event": spec.rte_event, "position": position,
    }


def _run_update_rte_configuration(rte_configuration) -> None:
    app = get_app()
    batch_mode = app.BatchMode
    app.BatchMode = True
    try:
        messages = rte_configuration.RunBswPlugin(_UPDATE_RTE_CONFIGURATION)
    finally:
        app.BatchMode = batch_mode
    error_messages = messages.ErrorMessages if messages is not None else None
    if error_messages is not None and error_messages.Count:
        raise PermanentError(
            f"'{_UPDATE_RTE_CONFIGURATION}' failed: "
            + "; ".join(f"{m.MessageIdentifier}: {m.MessageText}" for m in error_messages.Elements),
            error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
        )


def _set_expected_tick_duration(rte_configuration, task_name: str, period_seconds: float) -> None:
    for interaction in rte_configuration.RteOsInteractions.Elements:
        for activation in interaction.RteUsedOsActivations.Elements:
            if _short_name(activation.RteActivationOsTaskRef) == task_name:
                activation.RteExpectedTickDuration = period_seconds


def _next_position_in_task(rte_configuration, task_name: str) -> int:
    position = 0
    for instance in rte_configuration.RteSwComponentInstances.Elements:
        for mapping in instance.RteEventToTaskMappings.Elements:
            if _short_name(mapping.RteMappedToTaskRef) == task_name:
                position = max(position, mapping.RtePositionInTask + 1)
    for instance in rte_configuration.RteBswModuleInstances.Elements:
        for mapping in instance.RteBswEventToTaskMappings.Elements:
            if _short_name(mapping.RteBswMappedToTaskRef) == task_name:
                position = max(position, mapping.RteBswPositionInTask + 1)
    return position


def _entity_path(entity) -> Optional[str]:
    try:
        return entity.AUTOSARPathName
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _message_text(message) -> str:
    texts = [message.Text]
    for child in message.ChildMessages:
        texts.append(_message_text(child))
    return "\n".join(texts)


def _module_validation_errors(ecu_configuration, modules: list[Any]) -> list[dict[str, str]]:
    """Validate the ECU configuration and return the errors reported for the given modules."""
    module_paths = [module.AUTOSARPathName for module in modules]
    ecu_configuration.Validate.Do()
    errors = []
    for result in ecu_configuration.Validate.ResultStructure.RuleResults:
        if result.Severity != "Error":
            continue
        path = _entity_path(result.Entity)
        if path is None or not any(path == p or path.startswith(p + "/") for p in module_paths):
            continue
        errors.append({"message": f"{result.RuleId}: {_message_text(result.Message)}", "element": path})
    return errors


@mcp.tool(
    title="Configure Runnable Mapping",
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
def configure_runnable_mapping(  # pylint: disable=too-many-locals
    tasks: Annotated[Optional[list[OsTaskSpec]], Field(
        min_length=1,
        description=(
            "OS tasks to create, or to update if a task with the name exists. Omit (null) to only map "
            "events to existing tasks. At least one of tasks and mappings must be given. "
            "Example: [{'name': 'Task10ms', 'period_ms': 10, 'priority': 10}, "
            "{'name': 'TaskInit', 'period_ms': null, 'priority': 20}]"
        ),
    )] = None,
    mappings: Annotated[Optional[list[RunnableMappingSpec]], Field(
        min_length=1,
        description=(
            "RTE events of runnables to map to OS tasks. Omit (null) to only create or update tasks. "
            "At least one of tasks and mappings must be given. "
            "Example: [{'task': 'Task10ms', 'component': 'FuelsysController', 'runnable': 'AirflowCalculation', "
            "'rte_event': 'AirflowCalculation10ms', 'position': 0}]"
        ),
    )] = None,
    vecu_name: VecuNameParam = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the configuration to complete. Example: 240",
    )] = 240,
) -> dict[str, Any]:
    """
    Create OS tasks, map the RTE events of runnables to them and set their execution order (runnable schedule).

    Use it when the automatic runnable mapping of the V-ECU is not enough, e.g. to set your own
    task period or priority. Timing events and init events always have to be mapped; a timing
    event's period must be a multiple of its task's period. Call get_vecu_configuration first to
    get component, runnable and RTE event names.

    Existing tasks with the same name are updated (priority; period if given) and existing mappings
    of the given events are moved to the given task and position; nothing else is deleted.
    Runs SystemDesk's 'Update Rte Configuration' before mapping. Repeating the call with the same
    arguments leaves the configuration unchanged. Changes are not saved; use close_project(save_project=true).

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open.
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name, tasks, mappings, schedule_generated and errors. errors lists the errors that
    SystemDesk's ECU configuration validation reports for the Os and Rte modules, each with message
    and element (e.g. a timing period that is not a multiple of the task period, or a position used twice).

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - permanent / INVALID_ARGUMENTS: neither tasks nor mappings is given, a task name or a
      (component, rte_event) pair is given twice, or an RTE event does not start the given runnable
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: the ECU configuration has no Os or Rte module, a
      component or RTE event does not exist, or a mapping refers to a task that neither exists
      nor is given in tasks
    - permanent / SYSTEMDESK_OPERATION_FAILED: 'Update Rte Configuration' reported errors
    - transient / TIMEOUT: the configuration timed out
    - permanent / UNEXPECTED_ERROR: a SystemDesk call failed
    """
    try:
        if not tasks and not mappings:
            raise PermanentError(
                "Nothing to configure: give at least one of tasks and mappings.",
                error_code=ErrorCode.INVALID_ARGUMENTS,
            )
        tasks = tasks or []
        mappings = mappings or []

        v_ecu = get_vecu(vecu_name)
        ecu_configuration = v_ecu.EcuConfiguration
        os_configuration = _get_module_configuration(ecu_configuration, "Os")
        rte_configuration = _get_module_configuration(ecu_configuration, "Rte")

        events = _resolve_mapping_request(ecu_configuration, os_configuration, tasks, mappings)

        for spec in tasks:
            _create_or_update_task(os_configuration, spec)
        _run_update_rte_configuration(rte_configuration)
        for spec in tasks:
            if spec.period_ms is not None:
                _set_expected_tick_duration(rte_configuration, spec.name, spec.period_ms / 1000)

        applied = [
            _map_event(os_configuration, rte_configuration, spec, event)
            for spec, event in zip(mappings, events)
        ]
        task_results = []
        for spec in tasks:
            task = os_configuration.OsTasks.Item(spec.name)
            task_results.append({"name": spec.name, "period_ms": _task_period_ms(task), "priority": task.OsTaskPriority})

        return success_response({
            "vecu_name": v_ecu.Name,
            "tasks": task_results,
            "mappings": applied,
            "schedule_generated": True,
            "errors": _module_validation_errors(ecu_configuration, [os_configuration, rte_configuration]),
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to configure the runnable mapping.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


# --------------------------------------------------------------------------------------------------
# configure_rte_interventions
# --------------------------------------------------------------------------------------------------

PointKey = tuple[str, str, str, int]


def _unique_service_name(key: PointKey, existing_names: set[str]) -> str:
    """Return the service name of key, with the lowest numeric suffix (1, 2, ...) not in existing_names."""
    direction = "Input" if key[3] == _POINT_KIND_READ else "Output"
    base_name = f"{key[0]}_{key[1]}_{direction}Service"
    name = base_name
    suffix = 0
    while name in existing_names:
        suffix += 1
        name = f"{base_name}{suffix}"
    return name


def _collect_intervention_points(rte_interventions) -> tuple[dict[PointKey, list[Any]], dict[PointKey, list[Any]], set]:
    """Return possible points and unmapped existing points by key, and the targets of existing points."""
    possible: dict[PointKey, list[Any]] = {}
    for point in rte_interventions.GetPossibleInterventionPoints(_KIND_OPTION_READ_AND_WRITE_ONLY):
        key = _point_key(point)
        if key is not None:
            possible.setdefault(key, []).append(point)
    unmapped: dict[PointKey, list[Any]] = {}
    existing_targets = set()
    for point in rte_interventions.GetInterventionPoints():
        key = _point_key(point)
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


def _map_points_to_service(rte_interventions, key: PointKey, points: list[Any], access: str) -> list[str]:
    """Map each point to its own newly created service of key; return the names of the created services."""
    existing_names = {service.ShortName for service in rte_interventions.InterventionServices.Elements}
    created: list[str] = []
    for point in points:
        name = _unique_service_name(key, existing_names)
        service = rte_interventions.InterventionServices.AddNew(name)
        if key[3] == _POINT_KIND_READ:
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
    intervention_points: Annotated[Optional[list[InterventionPointSpec]], Field(
        description=(
            "Port data elements to create RTE intervention points for. Omitted or null: all possible points, "
            "as listed by get_vecu_configuration (rte_intervention_points). Empty list []: no points or "
            "services are created, e.g. to only set enable. "
            "Example: [{'component': 'FuelsysController', 'port': 'RpCorrectedSensors', 'data_element': 'Sensors'}]"
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
    The service signals are accessible through data access points (access='dap') from outside the
    V-ECU, or through RTE service ports (access='rte_service_port') inside the V-ECU.

    Existing intervention points and services are kept; already existing points are not created
    again, so repeating the call with the same arguments creates nothing new. The access of existing
    intervention services is never changed: access only applies to newly created points and to
    existing points without a service. Use get_vecu_configuration (rte_interventions) to check the
    access of existing points.
    Pass intervention_points=[] to create no points or services.
    Sets the V-ECU's RTE intervention code generation switch to enable (default true). enable does
    not affect which points are created: with enable=false, the points selected by
    intervention_points (all possible points if omitted) are still created, and existing points and
    services are kept. To only switch code generation off, pass enable=false and intervention_points=[].
    Changes are not saved; use close_project(save_project=true).

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

        possible, unmapped, existing_targets = _collect_intervention_points(rte_interventions)
        selected = _select_intervention_points(intervention_points, possible, unmapped, existing_targets)

        points_created: list[str] = []
        services_created: list[str] = []
        for key in sorted(selected):
            points = list(unmapped.get(key, []))
            if key in possible:
                created = list(rte_interventions.CreateInterventionPoints(possible[key]))
                points.extend(created)
                points_created.extend(p.ShortName for p in created)
            if points:
                services_created.extend(_map_points_to_service(rte_interventions, key, points, access))

        rte_interventions.InterventionEnableCodeGeneration = enable

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
