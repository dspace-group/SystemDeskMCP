"""V-ECU BSW generation and FMU export tools."""

import os
from typing import Annotated, Any, Optional

from pydantic import Field

from systemdesk_mcp.common import (
    ABSOLUTE_PATH_PATTERN,
    ErrorCode,
    PermanentError,
    TimeoutSeconds,
    VecuNameParam,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_active_project, get_app, get_vecu, logged, on_com_thread

_BSW_GENERATION_NAME = "V-ECU Implementation"

# Errors SystemDesk reports during every FMU export that do not prevent a usable FMU.
_EXPECTED_FMU_EXPORT_ERRORS = {
    "Error(102,110,5)",
    "Error(95,1140,108)",
    "Error(95,1140,151)",
}
_BUILDING_VECU_MESSAGE_ID = "Info(95,1140,143)"
_BUILD_LOG_MESSAGE_ID = "Info(102,110,13)"
_BUILD_LOG_MESSAGE_TEXT = "Writing the build log to:"


@mcp.tool(
    title="Generate V-ECU BSW",
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
def generate_vecu_bsw(
    vecu_name: VecuNameParam = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for BSW generation to complete. Example: 240",
    )] = 240,
) -> dict[str, Any]:
    """
    Run BSW generation ("Auto Configure And Generate") for the V-ECU Implementation of a V-ECU.

    Regenerates the BSW configuration and implementation of the V-ECU's ECU configuration;
    previously generated content of that V-ECU is overwritten. Repeating the call with the
    same vecu_name regenerates the same result.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open
    (e.g. created by get_or_create_vecu).
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name and a confirmation message on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - permanent / SYSTEMDESK_OPERATION_FAILED: BSW generation reported errors (see details)
    - transient / TIMEOUT: BSW generation timed out
    - permanent / UNEXPECTED_ERROR: BSW generation call failed
    """
    try:
        v_ecu = get_vecu(vecu_name)

        result = v_ecu.EcuConfiguration.StartBswGeneration(_BSW_GENERATION_NAME)

        error_messages = result.ErrorMessages
        if error_messages is None or error_messages.Count == 0:
            return success_response({
                "vecu_name": v_ecu.Name,
                "message": f"Execution of BSW generation for {_BSW_GENERATION_NAME} was successful.",
            })

        return error_response(
            f"Execution of BSW generation for {_BSW_GENERATION_NAME} failed.",
            error_type="permanent",
            error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
            details="\n".join(
                str(error_messages.Elements[index].MessageText)
                for index in range(error_messages.Count)
            ),
        )

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to run BSW generation for the V-ECU.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


@mcp.tool(
    title="Export V-ECU FMU",
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
def export_vecu_fmu(
    vecu_name: VecuNameParam = None,
    target_directory: Annotated[Optional[Annotated[str, Field(min_length=1, pattern=ABSOLUTE_PATH_PATTERN)]], Field(
        description=(
            "Absolute local or UNC path to the directory where the FMU file will be written. "
            "Defaults to the directory of the active project file. "
            "Example: 'C:\\Projects\\MyECU'"
        ),
    )] = None,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the export to complete. Example: 600",
    )] = 600,
) -> dict[str, Any]:
    """
    Export a V-ECU to an FMU file named <vecu_name>.fmu.

    Reuses the V-ECU's existing build script if it exists; otherwise deletes a stale default
    build script file and generates a new one. Then writes the FMU to target_directory, or next
    to the project file if omitted. An existing FMU file with the same name is overwritten.
    Repeating the call with the same arguments produces the same FMU file.

    Preconditions: start_systemdesk must be called first; a project with a V-ECU must be open
    (e.g. created by get_or_create_vecu); to export a working FMU, BSW generation should have
    been run (generate_vecu_bsw).
    vecu_name may be omitted only if the project contains exactly one V-ECU.
    Returns vecu_name, fmu_file path and a confirmation message on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / VECU_NOT_FOUND: vecu_name does not exist, or the project has no V-ECU
    - permanent / VECU_SELECTION_REQUIRED: vecu_name omitted but the project has several V-ECUs;
      ask the user to choose one of the listed V-ECUs and call again with vecu_name
    - permanent / SYSTEMDESK_OPERATION_FAILED: export reported errors; details list them
      or reference the build log
    - transient / TIMEOUT: export timed out
    - permanent / UNEXPECTED_ERROR: export call failed
    """
    try:
        active_project = get_active_project()
        v_ecu = get_vecu(vecu_name)

        target_dir = target_directory or os.path.dirname(active_project.File)
        fmu_file = os.path.abspath(os.path.join(target_dir, f"{v_ecu.Name}.fmu"))

        existing_build_script = None
        try:
            build_script_prop = v_ecu.BuildScript
            if build_script_prop is not None:
                existing_build_script = build_script_prop.Evaluate()
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # BuildScript property not available on this V-ECU type; generate a new one

        if not (existing_build_script and os.path.isfile(existing_build_script)):
            default_build_script_file = v_ecu.DefaultBuildScriptFile.Evaluate()
            if os.path.exists(default_build_script_file):
                os.remove(default_build_script_file)
            v_ecu.GenerateBuildScript()

        export_settings = v_ecu.GetNewExportFmuSettings()
        export_settings.TargetPath = fmu_file

        v_ecu.ExportFmu(export_settings)

        build_result = export_settings.Result
        if build_result.AnyError:
            unexpected_errors = _collect_unexpected_fmu_export_errors(build_result)
            if unexpected_errors:
                return error_response(
                    f"V-ECU FMU export failed for '{fmu_file}'. "
                    "Fix the reported errors and try again.",
                    error_type="permanent",
                    error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
                    details="\n".join(
                        f"{item.MessageIdentifier}: {item.MessageText}"
                        for item in unexpected_errors
                    ),
                )
            return error_response(
                f"V-ECU FMU export failed for '{fmu_file}'. "
                "Review the build log for details, fix the reported errors, and try again.",
                error_type="permanent",
                error_code=ErrorCode.SYSTEMDESK_OPERATION_FAILED,
                details=_get_fmu_export_log_location(),
            )

        return success_response({
            "vecu_name": v_ecu.Name,
            "fmu_file": fmu_file,
            "message": f"V-ECU FMU exported successfully to '{fmu_file}'.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to export the V-ECU FMU.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


def _collect_unexpected_fmu_export_errors(build_result) -> list[Any]:
    """Return the error messages (incl. children) that are not in the expected set."""
    unexpected_errors = []
    for error in build_result.Elements:
        if error.Severity != "Error":
            continue
        if error.MessageIdentifier in _EXPECTED_FMU_EXPORT_ERRORS:
            continue
        unexpected_errors.append(error)
        unexpected_errors.extend(error.Children.Elements)
    return unexpected_errors


def _get_fmu_export_log_location() -> str:
    for message in reversed(get_app().Messages.Elements):
        if message.MessageIdentifier == _BUILDING_VECU_MESSAGE_ID:
            build_log_message = _find_message_by_id_and_text(
                message, _BUILD_LOG_MESSAGE_ID, _BUILD_LOG_MESSAGE_TEXT,
            )
            if build_log_message is not None:
                return build_log_message.MessageText
            break
    return "Build log location not found. Check the SystemDesk message log for details."


def _find_message_by_id_and_text(message, identifier: str, text_fragment: str = ""):
    """Recursively search a SystemDesk message tree for the first matching message."""
    if message.MessageIdentifier == identifier and (not text_fragment or text_fragment in message.MessageText):
        return message
    for child in message.Children.Elements:
        if child.MessageIdentifier == identifier and (not text_fragment or text_fragment in child.MessageText):
            return child
        found = _find_message_by_id_and_text(child, identifier, text_fragment)
        if found is not None:
            return found
    return None
