"""AUTOSAR import, composition creation, validation, and report formatting."""

import os
from enum import Enum
from typing import Annotated, Any, Iterable

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    ARXML_PATTERN,
    AutosarElementPath,
    PermanentError,
    TimeoutSeconds,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_active_project, get_app, logged, on_com_thread


class ValidateOutputFormat(str, Enum):
    """Allowed output formats for validate_autosar."""

    MARKDOWN = "markdown"
    JSON = "json"


@mcp.tool(
    title="Import AUTOSAR File",
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
def import_autosar_file(
    file_path: Annotated[str, Field(
        min_length=1,
        pattern=ARXML_PATTERN,
        description=(
            "Absolute local or UNC path to the AUTOSAR file (.arxml) to import. "
            "Example: 'C:\\Projects\\MyECU\\SoftwareComponent.arxml'"
        ),
    )],
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the import to complete. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """
    Import an AUTOSAR file (.arxml) into the currently open SystemDesk project.

    Use validate_autosar to check the imported content for rule violations.
    Importing the same file twice has no effect (idempotent).

    Preconditions: start_systemdesk must be called first; a project must be open;
    file_path must point to an existing .arxml file.
    Returns imported_file path and a confirmation message on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / FILE_NOT_FOUND: file not found
    - permanent / NO_PROJECT_OPEN: no project currently open
    - permanent / IMPORT_REJECTED: SystemDesk rejected the file content (see details)
    - transient / TIMEOUT: import timed out
    - permanent / UNEXPECTED_ERROR: import call failed
    """
    try:
        active_project = get_active_project()

        if not os.path.isfile(file_path):
            raise PermanentError(
                f"File not found: '{file_path}'. "
                "Provide an absolute path to an existing .arxml file.",
                error_code=ErrorCode.FILE_NOT_FOUND)

        import_export_files = active_project.ImportExportFiles
        import_export_file = import_export_files.Item(file_path)
        if import_export_file is None:
            import_export_file = import_export_files.Add(file_path)
        import_export_file.AddNewElementsToConfiguration = True
        import_export_file.ExportDiagrams = True
        import_export_file.ImportDiagrams = True
        import_export_file.ImportAllElements = True
        import_export_file.ShowImportDialog = False

        # Now import the file. Runs on the dedicated COM apartment thread;
        # the timeout is enforced by the @on_com_thread decorator.
        result = import_export_file.Import()
        if not result:
            import_export_file.Delete()

            return error_response(
                f"Failed to import AUTOSAR file '{file_path}'.",
                error_type="permanent",
                error_code=ErrorCode.IMPORT_REJECTED,
                details=_get_import_autosar_file_error_details(),
            )

        return success_response({
            "imported_file": file_path,
            "message": (
                f"AUTOSAR file '{os.path.basename(file_path)}' imported successfully. "
                "Use validate_autosar to check for design rule violations."
            ),
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            f"Failed to import AUTOSAR file '{file_path}'.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )

def _get_import_autosar_file_error_details() -> str:
    app = get_app()
    last_message = app.Messages.Elements[-1]
    if last_message.MessageIdentifier != 'Info(93,600,17)': # ID of "Preparing import" message, stable across SystemDesk versions
        return "Unknown error"

    details = '\n'.join(_dump_message(last_message))
    return details

def _dump_message(message, indent=0) -> Iterable[str]:
    yield f'{" " * indent}[{message.Severity}] {message.MessageText}'

    for child in message.Children.Elements:
        yield from _dump_message(child, indent=indent + 2)


@mcp.tool(
    title="Validate AUTOSAR",
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
def validate_autosar(
    output_format: Annotated[ValidateOutputFormat, Field(
        description=(
            "Format for the validation result output. "
            "Accepted values: 'markdown', 'json'. "
            "Example: 'markdown'"
        ),
    )] = ValidateOutputFormat.MARKDOWN,
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for validation to complete. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Validate the currently open SystemDesk project against AUTOSAR design rules.

    Does not modify the project.

    Preconditions: start_systemdesk must be called first; a project must be open.
    Returns error_count, warning_count, total_count, and a report (markdown or list of messages).

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / NO_PROJECT_OPEN: no project currently open
    - transient / TIMEOUT: validation timed out
    - permanent / UNEXPECTED_ERROR: validation call failed
    """
    try:
        active_project = get_active_project()

        # Validation runs on the dedicated COM apartment thread; the timeout
        # is enforced by the @on_com_thread decorator.
        active_project.Validate.Do()

        rule_results = []
        for msg in active_project.Validate.ResultStructure.RuleResults:
            rule_results.append({
                "severity": msg.Severity,
                "rule": msg.RuleId,
                "description": msg.Message.Text,
                "description_details": _get_description_details(msg.Message),
                "element": msg.Entity.AUTOSARPathName if hasattr(msg.Entity, "AUTOSARPathName") else "N/A",
            })

        error_count = sum(1 for r in rule_results if r["severity"] == "Error")
        warning_count = sum(1 for r in rule_results if r["severity"] == "Warning")

        if output_format == ValidateOutputFormat.MARKDOWN:
            lines = [
                "# Validation Result",
                f"**Errors:** {error_count} | **Warnings:** {warning_count} | **Total:** {len(rule_results)}",
                "",
            ]
            if rule_results:
                lines.append("| Severity | Rule | Description | Element |")
                lines.append("|---|---|---|---|")
                for r in rule_results:
                    description_parts = [
                        r["description"],
                        *_flatten_description_details(r["description_details"]),
                    ]
                    description = "<br>".join(
                        _markdown_line_breaks(part) for part in description_parts
                    )
                    lines.append(
                        f"| {r['severity']} | {r['rule']} | {description} | {r['element']} |"
                    )
            else:
                lines.append("✅ No validation issues found.")

            return success_response({
                "error_count": error_count,
                "warning_count": warning_count,
                "total_count": len(rule_results),
                "report": "\n".join(lines),
            })

        return success_response({
            "error_count": error_count,
            "warning_count": warning_count,
            "total_count": len(rule_results),
            "messages": rule_results,
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to validate the project.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )

def _get_description_details(validation_message) -> list[dict[str, Any]]:
    return [
        {
            "text": child.Text,
            "children": _get_description_details(child),
        }
        for child in validation_message.ChildMessages
    ]

def _flatten_description_details(details: list[dict[str, Any]]) -> Iterable[str]:
    for detail in details:
        yield detail["text"]
        yield from _flatten_description_details(detail["children"])

def _markdown_line_breaks(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


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
