"""V-ECU resolution and creation tools."""
# pylint: disable=protected-access

import json
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from systemdesk_mcp import runtime
from systemdesk_mcp.common import (
    AutosarElementPath,
    AutosarShortName,
    ErrorCode,
    PermanentError,
    TimeoutSeconds,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_app, logged, on_com_thread
from systemdesk_mcp.tools.autosar import _resolve_component

_FALLBACK_COMPONENT_TYPES = (
    "COMPOSITION-SW-COMPONENT-TYPE",
    "APPLICATION-SW-COMPONENT-TYPE",
    "SENSOR-ACTUATOR-SW-COMPONENT-TYPE",
)


def _find_sw_component(active_project, component_path):
    """Return the SW component at component_path, else the first one of the fallback types."""
    if component_path:
        return _resolve_component(active_project, component_path)

    for type_name in _FALLBACK_COMPONENT_TYPES:
        components = active_project.FindChildElements(type_name)
        if components:
            return components[0]

    raise PermanentError(
        "No suitable SW component type was found in the active project.",
        error_code=ErrorCode.COMPONENT_NOT_FOUND)


def _get_active_project():
    active_project = get_app().ActiveProject
    if active_project is None:
        raise PermanentError(
            "No project is currently open. Call create_project or open a project first.",
            error_code=ErrorCode.NO_PROJECT_OPEN,
        )
    return active_project


def _collect_vecu_creation_messages() -> list[dict[str, Any]]:
    """Collect recent SystemDesk message entries related to V-ECU creation failures."""
    app = get_app()
    collected: list[dict[str, Any]] = []

    for message in reversed(app.Messages.Elements):
        error_messages = getattr(message, "ErrorMessages", None)
        if error_messages is None or not getattr(error_messages, "Count", 0):
            continue

        for index in range(error_messages.Count):
            item = error_messages.Elements[index]
            collected.append({
                "MessageIdentifier": getattr(item, "MessageIdentifier", None),
                "MessageText": getattr(item, "MessageText", None),
            })

        if collected:
            return collected

    return collected




def _create_vecu_options(rswc_type, vecu_name, system_name, settings: dict[str, Any]):
    """Return fresh V-ECU creation options for rswc_type with the given settings applied."""
    v_ecu_options = rswc_type.GetNewCreateVEcuOptions()
    if vecu_name is not None and getattr(v_ecu_options, "Ecu", None):
        prefix, _, _ = v_ecu_options.Ecu.rpartition("/")
        v_ecu_options.Ecu = prefix + "/" + vecu_name if prefix else vecu_name
    if system_name is not None:
        v_ecu_options.SystemName = system_name
    for attribute, value in settings.items():
        if value is not None:
            setattr(v_ecu_options, attribute, value)
    return v_ecu_options


def _find_vecu(active_project, vecu_name, reuse_existing_vecu):
    """Return the V-ECU named vecu_name, else the first V-ECU if reuse is requested, or None."""
    if vecu_name:
        try:
            return active_project.VEcus.Item(vecu_name)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
    if not reuse_existing_vecu:
        return None
    existing_vecus = active_project.VEcus.Elements
    return existing_vecus[0] if existing_vecus else None


def _vecu_response(v_ecu_obj: Any) -> dict[str, Any]:
    return {
        "vecu_name": getattr(v_ecu_obj, "Name", None),
        "system_path": getattr(getattr(v_ecu_obj, "System", None), "AUTOSARPathName", None),
        "ecu_configuration": getattr(v_ecu_obj, "EcuConfiguration", None),
    }


@mcp.tool(
    title="Get or Create V-ECU",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    output_schema=None
)
@logged
@on_com_thread
def get_or_create_vecu(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    vecu_name: Annotated[Optional[AutosarShortName], Field(
        description=(
            "AUTOSAR short name of the V-ECU to reuse if it exists, otherwise of the V-ECU to create. "
            "If omitted, a new V-ECU with the SystemDesk default name is created "
            "(see reuse_existing_vecu). Example: 'MyVEcu'"
        ),
    )] = None,
    reuse_existing_vecu: Annotated[bool, Field(
        description=(
            "Only relevant if vecu_name is omitted. Set to true only if the user explicitly asks to use "
            "an existing V-ECU; the first V-ECU in the project is then reused. Example: false"
        ),
    )] = False,
    component_path: Annotated[Optional[AutosarElementPath], Field(
        description=(
            "Absolute AUTOSAR path of the SwComponentType to create the V-ECU from, e.g. a composition "
            "created by create_composition. Defaults to the first composition, else the first application, "
            "else the first sensor-actuator SW component type in the project. Example: '/SWA/MyComposition'"
        ),
    )] = None,
    system_name: Annotated[Optional[AutosarShortName], Field(
        description="AUTOSAR short name of the system to create. Example: 'MySystem'",
    )] = None,
    predefined_ecu_configuration: Annotated[Literal[
        "Default ECU Configuration for V-ECU FMUs (FMI 2.0)",
        "Default ECU Configuration for V-ECU FMUs with Any Network (FMI 2.0)",
        "Default ECU Configuration for V-ECU FMUs with CAN Driver (FMI 2.0)",
        "Default ECU Configuration for V-ECU FMUs (FMI 3.0)",
        "Default ECU Configuration for V-ECU FMUs with Any Network (FMI 3.0)",
        "Default ECU Configuration for V-ECU FMUs with CAN Driver (FMI 3.0)",
        "Default ECU Configuration for V-ECU FMUs with CAN Driver (FMI 3.0 with FMI-LS-BUS)",
    ], Field(
        description=(
            "Exact name of the predefined ECU configuration. If the user made a typo or only described "
            "what they want (e.g. with/without network, FMI 2/3), choose the closest matching value. "
            "Example: 'Default ECU Configuration for V-ECU FMUs (FMI 3.0)'"
        ),
    )] = "Default ECU Configuration for V-ECU FMUs (FMI 3.0)",
    generate_dap_for_atomic_ports: Annotated[Optional[bool], Field(
        description="Generate DAP for atomic ports. Example: true",
    )] = None,
    generate_dap_for_root_composition_ports: Annotated[Optional[bool], Field(
        description="Generate DAP for root composition ports. Example: true",
    )] = None,
    create_runnable_mapping: Annotated[Optional[bool], Field(
        description="Create the runnable mapping. Example: true",
    )] = None,
    rte_calibration_support: Annotated[Optional[Literal["INITIALIZED_RAM", "NONE", "OFFLINE"]], Field(
        description=(
            "RTE calibration support. 'INITIALIZED_RAM': values can be changed at run time "
            "(software support necessary). 'NONE': no calibration support. "
            "'OFFLINE': values can be changed in the HEX file and flashed to the ECU. "
            "Example: 'NONE'"
        ),
    )] = None,
    rte_simulation_logging: Annotated[Optional[Literal["NONE", "SIMULATION"]], Field(
        description=(
            "RTE simulation logging. 'NONE': only variables with measurement access specified in the "
            "software architecture can be measured in a simulation. 'SIMULATION': global variables are "
            "generated for all data elements, inter-runnable variables and operation arguments, so all "
            "of them can be measured in a simulation. Example: 'SIMULATION'"
        ),
    )] = None,
    rte_measurement_support: Annotated[Optional[bool], Field(
        description="Enable RTE measurement support. Example: true",
    )] = None,
    auto_configure_and_generate: Annotated[bool, Field(
        description="Whether to auto-configure and generate the V-ECU. Example: false",
    )] = False,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for V-ECU lookup/creation to complete. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Resolve the V-ECU to work with: reuse an existing one or create a new one.

    Reuse: vecu_name if it exists. Without vecu_name, the first V-ECU in the project is reused
    only if reuse_existing_vecu is true.
    Creation (no V-ECU reused): from component_path if given, else the first composition,
    application, or sensor-actuator SW component type (in this order). The new V-ECU is named
    vecu_name if given. The creation options (system_name, predefined_ecu_configuration, ...)
    only apply to creation; options left unset keep the SystemDesk defaults.
    Creation is additive: SystemDesk creates the V-ECU elements in a new package and never
    overwrites existing elements, even if system_name or vecu_name clash with existing names.
    Repeated calls: with vecu_name, later calls reuse the V-ECU created by the first call.
    Without vecu_name and with reuse_existing_vecu false, each call creates a new V-ECU.

    Preconditions: start_systemdesk must be called first; a project must be open.
    Returns the resolved V-ECU name, system path, ECU configuration, and whether it was newly created;
    on creation also the component_path the V-ECU was created from.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / AUTOSAR_ELEMENT_NOT_FOUND: component_path does not exist
    - permanent / AMBIGUOUS_AUTOSAR_PATH: component_path resolves to multiple elements
    - permanent / INVALID_AUTOSAR_ELEMENT_TYPE: component_path is not a SwComponentType
    - permanent / COMPONENT_NOT_FOUND: no component_path given and no suitable SW component found
      (when creation is needed)
    - permanent / VECU_CREATION_FAILED: SystemDesk rejected the V-ECU creation (see details)
    - transient / TIMEOUT: V-ECU lookup or creation timed out
    - permanent / UNEXPECTED_ERROR: V-ECU lookup or creation call failed
    """
    try:
        active_project = _get_active_project()

        v_ecu = _find_vecu(active_project, vecu_name, reuse_existing_vecu)
        if v_ecu is not None:
            runtime._last_vecu = v_ecu
            return success_response({
                **_vecu_response(v_ecu),
                "created": False,
                "message": f"Reusing existing V-ECU '{getattr(v_ecu, 'Name', None)}'.",
            })

        rswc_type = _find_sw_component(active_project, component_path)
        v_ecu_options = _create_vecu_options(rswc_type, vecu_name, system_name, {
            "PredefinedEcuConfiguration": predefined_ecu_configuration,
            "GenerateDapForAtomicPorts": generate_dap_for_atomic_ports,
            "GenerateDapForRootCompositionPorts": generate_dap_for_root_composition_ports,
            "CreateRunnableMapping": create_runnable_mapping,
            "RteCalibrationSupport": rte_calibration_support,
            "RteSimulationLogging": rte_simulation_logging,
            "RteMeasurementSupport": rte_measurement_support,
            "AutoConfigureAndGenerate": auto_configure_and_generate,
        })

        v_ecu = rswc_type.CreateVEcu(v_ecu_options)
        if v_ecu is None:
            collected = _collect_vecu_creation_messages()
            return error_response(
                "V-ECU creation failed. SystemDesk reported the following issue(s):",
                error_type="permanent",
                error_code=ErrorCode.VECU_CREATION_FAILED,
                details=json.dumps(collected, default=str) if collected else None,
            )

        runtime._last_vecu = v_ecu

        return success_response({
            **_vecu_response(v_ecu),
            "created": True,
            "component_path": getattr(rswc_type, "AUTOSARPathName", None),
            "message": "V-ECU created successfully.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to get or create the V-ECU.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )
