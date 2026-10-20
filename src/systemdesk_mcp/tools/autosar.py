"""AUTOSAR import, validation, and report formatting."""

import os
from enum import Enum
from typing import Annotated, Any, Iterable

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    ARXML_PATTERN,
    PermanentError,
    TimeoutSeconds,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_app, logged, on_com_thread


class ValidateOutputFormat(str, Enum):
    """Allowed output formats for validate_autosar."""

    MARKDOWN = "markdown"
    JSON = "json"



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
    - transient / IMPORT_FAILED: import call failed
    """
    try:
        app = get_app()

        if not os.path.isfile(file_path):
            raise PermanentError(
                f"File not found: '{file_path}'. "
                "Provide an absolute path to an existing .arxml file.",
                error_code=ErrorCode.FILE_NOT_FOUND)

        active_project = app.ActiveProject
        if active_project is None:
            raise PermanentError(
                "No project is currently open. "
                "Call create_project or open a project first.",
                error_code=ErrorCode.NO_PROJECT_OPEN)

        import_export_file = active_project.ImportExportFiles.Add(file_path)
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
            error_type="transient",
            error_code=ErrorCode.IMPORT_FAILED,
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
    - transient / VALIDATION_FAILED: validation call failed
    """
    try:
        app = get_app()

        active_project = app.ActiveProject
        if active_project is None:
            raise PermanentError(
                "No project is currently open. Call create_project or open a project first.",
                error_code=ErrorCode.NO_PROJECT_OPEN)

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
            error_type="transient",
            error_code=ErrorCode.VALIDATION_FAILED,
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
