"""V-ECU options creation and V-ECU resolution tools."""
# pylint: disable=protected-access

import json
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from systemdesk_mcp import runtime
from systemdesk_mcp.common import (
    ErrorCode,
    PermanentError,
    TimeoutSeconds,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_app, logged, on_com_thread

_COMPOSITION_COMPONENT_TYPE = "COMPOSITION-SW-COMPONENT-TYPE"


def _find_sw_component(active_project, component_name, component_type):
    """Return the SW component named component_name, else the first one of component_type."""
    candidate_type_names = (
        [component_type]
        if component_type != _COMPOSITION_COMPONENT_TYPE
        else [
            _COMPOSITION_COMPONENT_TYPE,
            "APPLICATION-SW-COMPONENT-TYPE",
            "SENSOR-ACTUATOR-SW-COMPONENT-TYPE",
        ]
    )

    rswc_type = None
    if component_name:
        for type_name in candidate_type_names:
            components = active_project.FindChildElements(type_name)
            for component_type_element in components:
                component_name_value = getattr(component_type_element, "ShortName", None)
                if component_name_value == component_name and component_name_value != "EcuFlatView":
                    rswc_type = component_type_element
                    break
            if rswc_type is not None:
                break
        if rswc_type is None:
            raise PermanentError(
                f"No SW component named '{component_name}' was found in the active project.",
                error_code=ErrorCode.COMPONENT_NOT_FOUND)
    else:
        for type_name in candidate_type_names:
            components = active_project.FindChildElements(type_name)
            if components:
                rswc_type = components[0]
                break

    if rswc_type is None:
        raise PermanentError(
            "No suitable SW component type was found in the active project.",
            error_code=ErrorCode.COMPONENT_NOT_FOUND)
    return rswc_type


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


@mcp.tool(
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
def create_vecu_options(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    component_name: Annotated[Optional[str], Field(
        description=(
            "SW component to create the V-ECU from. Defaults to the first component of component_type. "
            "Example: 'MyComposition'"
        ),
    )] = None,
    component_type: Annotated[str, Field(
        description=(
            "AUTOSAR SW component type to search. "
            "Example: 'APPLICATION-SW-COMPONENT-TYPE'"
        ),
    )] = _COMPOSITION_COMPONENT_TYPE,
    vecu_name: Annotated[Optional[str], Field(
        description="Name of the V-ECU to create. Example: 'MyVEcu'",
    )] = None,
    system_name: Annotated[Optional[str], Field(
        description="Name of the system to create. Example: 'MySystem'",
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
        description="Generate DAP for atomic ports. Example: True",
    )] = None,
    generate_dap_for_root_composition_ports: Annotated[Optional[bool], Field(
        description="Generate DAP for root composition ports. Example: True",
    )] = None,
    create_runnable_mapping: Annotated[Optional[bool], Field(
        description="Create the runnable mapping. Example: True",
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
        description="Enable RTE measurement support. Example: True",
    )] = None,
    auto_configure_and_generate: Annotated[bool, Field(
        description="Whether to auto-configure and generate the V-ECU. Example: False",
    )] = False,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the options to be created. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """
    Build the V-ECU creation options for a SW component.

    Required before get_or_create_vecu_fmu can create a new V-ECU.
    The component is component_name if given, else the first component of component_type.
    With the default composition type, application and then sensor-actuator types are
    searched too if no composition exists. Options left unset keep the SystemDesk defaults.
    Pass the same component_name/component_type to get_or_create_vecu_fmu.

    Preconditions: start_systemdesk must be called first; a project must be open.
    Returns the component name and a confirmation message on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / COMPONENT_NOT_FOUND: component_name given but no such component exists,
      or no suitable SW component found
    - transient / TIMEOUT: options creation timed out
    - transient / VECU_OPTIONS_CREATION_FAILED: options creation call failed
    """
    try:
        active_project = _get_active_project()

        rswc_type = _find_sw_component(active_project, component_name, component_type)

        v_ecu_options = rswc_type.GetNewCreateVEcuOptions()
        if vecu_name is not None and getattr(v_ecu_options, "Ecu", None):
            prefix, _, _ = v_ecu_options.Ecu.rpartition("/")
            v_ecu_options.Ecu = prefix + "/" + vecu_name if prefix else vecu_name
        if system_name is not None:
            v_ecu_options.SystemName = system_name
        v_ecu_options.PredefinedEcuConfiguration = predefined_ecu_configuration
        if generate_dap_for_atomic_ports is not None:
            v_ecu_options.GenerateDapForAtomicPorts = generate_dap_for_atomic_ports
        if generate_dap_for_root_composition_ports is not None:
            v_ecu_options.GenerateDapForRootCompositionPorts = generate_dap_for_root_composition_ports
        if create_runnable_mapping is not None:
            v_ecu_options.CreateRunnableMapping = create_runnable_mapping
        if rte_calibration_support is not None:
            v_ecu_options.RteCalibrationSupport = rte_calibration_support
        if rte_simulation_logging is not None:
            v_ecu_options.RteSimulationLogging = rte_simulation_logging
        if rte_measurement_support is not None:
            v_ecu_options.RteMeasurementSupport = rte_measurement_support
        v_ecu_options.AutoConfigureAndGenerate = auto_configure_and_generate

        runtime._vecu_options = v_ecu_options

        return success_response({
            "component_name": getattr(rswc_type, "ShortName", None),
            "message": "V-ECU options created. Call get_or_create_vecu_fmu to create the V-ECU.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to create the V-ECU options.",
            error_type="transient",
            error_code=ErrorCode.VECU_OPTIONS_CREATION_FAILED,
            details=str(exc),
        )


def _vecu_response(v_ecu_obj: Any) -> dict[str, Any]:
    return {
        "vecu_name": getattr(v_ecu_obj, "Name", None),
        "system_path": getattr(getattr(v_ecu_obj, "System", None), "AUTOSARPathName", None),
        "ecu_configuration": getattr(v_ecu_obj, "EcuConfiguration", None),
    }


@mcp.tool(
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
def get_or_create_vecu_fmu(
    vecu_name: Annotated[Optional[str], Field(
        description="Existing V-ECU to reuse. Example: 'MyVEcu'",
    )] = None,
    component_name: Annotated[Optional[str], Field(
        description="SW component to create the V-ECU from. Example: 'MyComposition'",
    )] = None,
    component_type: Annotated[str, Field(
        description=(
            "AUTOSAR SW component type to search. "
            "Example: 'APPLICATION-SW-COMPONENT-TYPE'"
        ),
    )] = _COMPOSITION_COMPONENT_TYPE,
    force_create: Annotated[bool, Field(
        description="Create a new V-ECU even if one exists. Example: False",
    )] = False,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for V-ECU lookup/creation to complete. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Resolve the V-ECU to work with: reuse an existing one or create a new one.

    Reuse (skipped if force_create): vecu_name if given, else the first V-ECU in the project.
    Creation: from the component found as in create_vecu_options, using the options prepared
    by that tool (required for creation, not needed for reuse).

    Preconditions: start_systemdesk must be called first; a project must be open.
    Returns the resolved V-ECU name, system path, ECU configuration, and whether it was newly created.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name given but no V-ECU with that name exists
    - permanent / VECU_OPTIONS_MISSING: create_vecu_options not called yet (when creation is needed)
    - permanent / COMPONENT_NOT_FOUND: component_name given but no such component exists,
      or no suitable SW component found (when creation is needed)
    - transient / TIMEOUT: V-ECU lookup or creation timed out
    - transient / VECU_CREATION_FAILED: V-ECU lookup or creation failed
    """
    try:
        active_project = _get_active_project()

        if not force_create:
            if vecu_name:
                try:
                    v_ecu = active_project.VEcus.Item(vecu_name)
                except Exception as exc:
                    raise PermanentError(
                        f"No V-ECU named '{vecu_name}' was found in the active project.",
                        error_code=ErrorCode.VECU_NOT_FOUND) from exc
            else:
                existing_vecus = active_project.VEcus.Elements
                v_ecu = existing_vecus[0] if existing_vecus else None

            if v_ecu is not None:
                runtime._last_vecu = v_ecu
                return success_response({
                    **_vecu_response(v_ecu),
                    "created": False,
                    "message": f"Reusing existing V-ECU '{getattr(v_ecu, 'Name', None)}'.",
                })

        if runtime._vecu_options is None:
            raise PermanentError(
                "No V-ECU options are available to create a V-ECU. Call create_vecu_options first.",
                error_code=ErrorCode.VECU_OPTIONS_MISSING)

        rswc_type = _find_sw_component(active_project, component_name, component_type)

        v_ecu = rswc_type.CreateVEcu(runtime._vecu_options)
        if v_ecu is None:
            collected = _collect_vecu_creation_messages()
            return error_response(
                "V-ECU creation failed. SystemDesk reported the following issue(s):",
                error_type="transient",
                error_code=ErrorCode.VECU_CREATION_FAILED,
                details=json.dumps(collected, default=str) if collected else None,
            )

        runtime._last_vecu = v_ecu

        return success_response({
            **_vecu_response(v_ecu),
            "created": True,
            "message": "V-ECU created successfully.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to get or create the V-ECU.",
            error_type="transient",
            error_code=ErrorCode.VECU_CREATION_FAILED,
            details=str(exc),
        )
