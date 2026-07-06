"""
SystemDesk MCP Server

Provides MCP tools for automating dSPACE SystemDesk via its COM automation interface.
Covers lifecycle management, project operations, and AUTOSAR file handling.
"""

import atexit
import json
import os
import concurrent.futures
import functools
import hashlib
import inspect
import logging
import time
import uuid
from enum import Enum
from typing import Annotated, Any, Optional

import pythoncom
from fastmcp import FastMCP
from pydantic import BaseModel, Field

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


mcp = FastMCP(
    "SystemDesk MCP Server",
    instructions=(
        "Use start_systemdesk before calling any other tool. "
        "Use close_systemdesk to release the COM connection when done. "
        "AUTOSAR files must use the .arxml extension. "
        "All tools return a JSON dictionary with a 'status' field ('success' or 'error'). "
        "On error, 'error_type' is either 'permanent' (do not retry, halt or escalate) "
        "or 'transient' (retryable with backoff)."
    ),
)

# ---------------------------------------------------------------------------
# COM connection state (module-level singleton)
# ---------------------------------------------------------------------------

_application = None  # holds the COM IApplication object


def _get_app():
    """Return the active SystemDesk COM connection or raise a permanent error."""
    if _application is None:
        raise _PermanentError(
            "SystemDesk is not running. Call start_systemdesk first."
        )
    return _application


class _PermanentError(Exception):
    """Signals a permanent (non-retryable) failure."""


class _TransientError(Exception):
    """Signals a transient (retryable) failure."""


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", **data}


def _error(
    message: str,
    error_type: str = "permanent",
    details: Optional[str] = None,
) -> dict[str, Any]:
    payload = {"status": "error", "error_type": error_type, "message": message}
    if details:
        payload["details"] = details
    return payload


def _com_thread_init():
    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)


_com_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="systemdesk-com",
    initializer=_com_thread_init,
)


@atexit.register
def _shutdown_com_executor():
    try:
        def _cleanup():
            global _application
            _application = None  # release COM proxy before uninitialising
            pythoncom.CoUninitialize()
        _com_executor.submit(_cleanup).result(timeout=5)
    except Exception:
        pass
    _com_executor.shutdown(wait=False)


def _on_com_thread(fn):
    """Run a tool's COM work on the single dedicated COM apartment thread.

    Honors an optional ``timeout`` argument; on expiry the worker keeps
    running the (serialized) COM call but the caller gets a transient error.
    """
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        timeout = bound.arguments.get("timeout")
        future = _com_executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return _error(
                f"Operation '{fn.__name__}' timed out"
                + (f" after {timeout}s." if timeout else ".")
                + " SystemDesk may still be processing the request.",
                error_type="transient",
            )

    return wrapper


_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _logged(fn):
    """Emit a structured JSON log entry for every tool invocation."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        correlation_id = str(uuid.uuid4())
        input_hash = hashlib.sha256(
            json.dumps(kwargs, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        t0 = time.monotonic()
        result = fn(*args, **kwargs)
        duration_ms = round((time.monotonic() - t0) * 1000)
        try:
            if isinstance(result, dict):
                outcome = result.get("status", "unknown")
            else:
                outcome = json.loads(result).get("status", "unknown")
        except Exception:
            outcome = "unknown"
        _logger.info(json.dumps({
            "correlation_id": correlation_id,
            "tool": fn.__name__,
            "version": __version__,
            "input_hash": input_hash,
            "duration_ms": duration_ms,
            "outcome": outcome,
        }))
        return result
    return wrapper


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class CloseSystemDeskInput(BaseModel):
    save_project: bool = Field(
        default=True,
        description=(
            "If True, the currently open project is saved before SystemDesk is closed. "
            "Example: True"
        ),
    )


class CreateProjectInput(BaseModel):
    project_path: str = Field(
        description=(
            "Absolute path to the directory where the project file will be created. "
            "Example: 'C:\\Projects\\MyECU'"
        )
    )
    project_name: str = Field(
        description=(
            "Name of the new SystemDesk project (without file extension). "
            "Example: 'MyECU_Architecture'"
        )
    )


class CloseProjectInput(BaseModel):
    save_project: bool = Field(
        default=True,
        description=(
            "If True, saves the project before closing it. "
            "Example: True"
        ),
    )


class ImportAutosarFileInput(BaseModel):
    file_path: str = Field(
        description=(
            "Absolute path to the AUTOSAR file (.arxml) to import. "
            "Example: 'C:\\Projects\\MyECU\\SoftwareComponent.arxml'"
        )
    )
    timeout: int = Field(
        default=60,
        description="Maximum number of seconds to wait for the import to complete. Example: 60",
    )


class ValidateOutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class ValidateAutosarInput(BaseModel):
    output_format: ValidateOutputFormat = Field(
        default=ValidateOutputFormat.MARKDOWN,
        description=(
            "Format for the validation result output. "
            "Accepted values: 'markdown', 'json'. "
            "Example: 'markdown'"
        ),
    )
    timeout: int = Field(
        default=120,
        description="Maximum number of seconds to wait for validation to complete. Example: 120",
    )


# ---------------------------------------------------------------------------
# SystemDesk Lifecycle Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def start_systemdesk(
) -> dict[str, Any]:
    """
    Start SystemDesk and establish a COM connection.

    If SystemDesk is already running, reconnects to the existing instance.
    Must be called before using any other SystemDesk tool.

    Returns the ApplicationRootDir and the resolved ProgID on success.

    Failure codes (error_type / condition):
    - permanent / win32com not installed
    - permanent / SystemDesk ProgID not registered
    - permanent / access denied
    - transient / SystemDesk blocked by open dialog

    Hint: This tool does not provide a timeout parameter,
    because opening the COM connection has an inherent timeout.
    """
    global _application
    try:
        import win32com.client
    except ImportError:
        return _error(
            "win32com is not installed. Install pywin32 to use this tool.",
            error_type="permanent",
        )

    prog_id = "SystemDesk.Application"

    # Check if already connected
    if _application is not None:
        try:
            root_dir = _application.ApplicationRootDir
            return _ok({
                "prog_id": prog_id,
                "application_root_dir": root_dir,
                "message": "SystemDesk is already running. Reusing existing COM connection.",
            })
        except Exception:
            _application = None  # stale connection, reconnect below

    try:
        _application = win32com.client.Dispatch(prog_id)
        _application.BatchMode = True  # suppresses popups for automation
        root_dir = _application.ApplicationRootDir
        return _ok({
            "prog_id": prog_id,
            "application_root_dir": root_dir,
            "message": "SystemDesk started and COM connection established.",
        })
    except Exception as exc:
        exc_str = str(exc)
        _application = None

        if "blocked" in exc_str.lower() or "rejected" in exc_str.lower():
            message = (
                "SystemDesk blocked the automation request. "
                "A modal dialog (e.g. license warning, unsaved project prompt) may be open in SystemDesk. "
                "Please close any open dialogs in SystemDesk and try again."
            )
            error_type = "transient"
        elif "class not registered" in exc_str.lower() or "invalid class string" in exc_str.lower():
            message = (
                f"SystemDesk is not installed or the ProgID '{prog_id}' is not registered. "
                "Verify the SystemDesk installation."
            )
            error_type = "permanent"
        elif "access" in exc_str.lower() or "permission" in exc_str.lower():
            message = (
                "Access denied when connecting to SystemDesk. "
                "Try running the MCP server with elevated privileges."
            )
            error_type = "permanent"
        else:
            message = (
                "Failed to start SystemDesk. "
                "Ensure SystemDesk is installed and no blocking dialog is open."
            )
            error_type = "transient"

        return _error(message, error_type=error_type, details=exc_str)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def close_systemdesk(
    save_project: Annotated[bool, Field(
        description="If True, the currently open project is saved before SystemDesk is closed. Example: True",
    )] = True,
) -> dict[str, Any]:
    """
    Close SystemDesk and release the COM connection.

    Optionally saves the open project before closing.
    After this call, start_systemdesk must be called again before using other tools.

    Precondition: start_systemdesk must be called first.
    Returns a confirmation message on success.

    Failure codes (error_type / condition):
    - permanent / SystemDesk not running (start_systemdesk not called)
    - transient / failed to save project before closing
    - transient / Quit call failed (returns immediately)
    """
    params = CloseSystemDeskInput(save_project=save_project)
    global _application
    try:
        app = _get_app()

        if params.save_project:
            try:
                active_project = app.ActiveProject
                if active_project is not None:
                    active_project.Save()
            except Exception as exc:
                return _error(
                    "Failed to save project before closing SystemDesk.",
                    error_type="transient",
                    details=str(exc),
                )

        app.Quit()
        _application = None
        return _ok({"message": "SystemDesk closed successfully."})

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent")
    except Exception as exc:
        return _error(
            "Failed to close SystemDesk.",
            error_type="transient",
            details=str(exc),
        )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def get_systemdesk_status() -> dict[str, Any]:
    """
    Return current SystemDesk status.

    Reports whether SystemDesk is running (COM connection established)
    and whether a project is currently open.

    Precondition: None, may be called at any time.
    Returns running, project_open, and optional project_name.

    Failure codes (error_type / condition): None
    """
    global _application

    if _application is None:
        return _ok(
            {
                "running": False,
                "project_open": False,
                "project_name": None,
                "message": "SystemDesk is not running.",
            }
        )

    try:
        active_project = _application.ActiveProject
        if active_project is None:
            return _ok(
                {
                    "running": True,
                    "project_open": False,
                    "project_name": None,
                    "message": "SystemDesk is running and no project is open.",
                }
            )

        return _ok(
            {
                "running": True,
                "project_open": True,
                "project_name": active_project.Name,
                "message": "SystemDesk is running and a project is open.",
            }
        )
    except Exception:
        # Stale COM proxy or process closed unexpectedly.
        _application = None
        return _ok(
            {
                "running": False,
                "project_open": False,
                "project_name": None,
                "message": "SystemDesk is not running.",
            }
        )


# ---------------------------------------------------------------------------
# Project Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def create_project(
    project_path: Annotated[str, Field(
        description="Absolute path to the directory where the project file will be created. Example: 'C:\\Projects\\MyECU'",
    )],
    project_name: Annotated[str, Field(
        description="Name of the new SystemDesk project (without file extension). Example: 'MyECU_Architecture'",
    )],
) -> dict[str, Any]:
    """
    Create a new SystemDesk project at the specified path.

    The project directory is created automatically if it does not exist.
    The resulting project file will be located at <project_path>/<project_name>.sdp.
    If a project with the same name already exists, it will be overwritten.
    Automatically closes any currently open project before creating the new one,
    which can lead to data loss if the current project is not saved.

    Precondition: start_systemdesk must be called first.
    Returns project_name and project_file path on success.

    Failure codes (error_type / condition):
    - permanent / SystemDesk not running (start_systemdesk not called)
    - transient / project creation or save failed
    """
    params = CreateProjectInput(project_path=project_path, project_name=project_name)
    try:
        app = _get_app()

        project_dir = params.project_path
        project_file = os.path.join(project_dir, f"{params.project_name}.sdp")

        os.makedirs(project_dir, exist_ok=True)

        project = app.CreateProject(project_file)
        project.Save()

        return _ok({
            "project_name": params.project_name,
            "project_file": project_file,
            "message": f"Project '{params.project_name}' created successfully.",
        })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent")
    except Exception as exc:
        return _error(
            f"Failed to create project '{params.project_name}'.",
            error_type="transient",
            details=str(exc),
        )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def close_project(
    save_project: Annotated[bool, Field(
        description="If True, saves the project before closing it. Example: True",
    )] = True,
) -> dict[str, Any]:
    """
    Close the currently open SystemDesk project.

    Optionally saves the project before closing.
    After closing, a new project can be created or opened.

    Precondition: start_systemdesk must be called first.
    Returns project_name and saved flag on success.

    Failure codes (error_type / condition):
    - permanent / SystemDesk not running (start_systemdesk not called)
    - transient / close or save call failed
    """
    params = CloseProjectInput(save_project=save_project)
    try:
        app = _get_app()
        active_project = app.ActiveProject

        if active_project is None:
            return _ok({"message": "No project is currently open."})

        project_name = active_project.Name

        if params.save_project:
            active_project.Save()

        active_project.Close(False)

        return _ok({
            "project_name": project_name,
            "saved": params.save_project,
            "message": f"Project '{project_name}' closed successfully.",
        })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent")
    except Exception as exc:
        return _error(
            "Failed to close the project.",
            error_type="transient",
            details=str(exc),
        )


# ---------------------------------------------------------------------------
# AUTOSAR File Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def import_autosar_file(
    file_path: Annotated[str, Field(
        description="Absolute local path to the AUTOSAR file (.arxml) to import. Example: 'C:\\Projects\\MyECU\\SoftwareComponent.arxml'",
    )],
    timeout: Annotated[int, Field(
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

    Failure codes (error_type / condition):
    - permanent / SystemDesk not running (start_systemdesk not called)
    - permanent / file not found
    - permanent / unsupported file extension (non-.arxml)
    - permanent / no project currently open
    - transient / import timed out
    - transient / import call failed
    """
    params = ImportAutosarFileInput(file_path=file_path, timeout=timeout)
    try:
        app = _get_app()

        if not os.path.isfile(params.file_path):
            return _error(
                f"File not found: '{params.file_path}'. "
                "Provide an absolute path to an existing .arxml file.",
                error_type="permanent",
            )

        if not params.file_path.lower().endswith(".arxml"):
            return _error(
                f"Unsupported file extension for '{params.file_path}'. "
                "Only .arxml files are supported.",
                error_type="permanent",
            )

        active_project = app.ActiveProject
        if active_project is None:
            return _error(
                "No project is currently open. Call create_project or open a project first.",
                error_type="permanent",
            )

        importDiagrams = True  # could be made an input parameter if needed
        optionShowImportDialog=False  # set to True to show the standard import dialog (not recommended for automation)

        importExportFile = active_project.ImportExportFiles.Add(params.file_path)
        importExportFile.AddNewElementsToConfiguration = True
        importExportFile.ExportDiagrams = importDiagrams
        importExportFile.ImportDiagrams = importDiagrams
        importExportFile.ImportAllElements = True
        importExportFile.ShowImportDialog = optionShowImportDialog

        # Now import the file. Runs on the dedicated COM apartment thread;
        # the timeout is enforced by the @_on_com_thread decorator.
        importExportFile.Import()
        return _ok({
            "imported_file": params.file_path,
            "message": (
                f"AUTOSAR file '{os.path.basename(params.file_path)}' imported successfully. "
                "Use validate_autosar to check for design rule violations."
            ),
        })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent")
    except Exception as exc:
        return _error(
            f"Failed to import AUTOSAR file '{params.file_path}'.",
            error_type="transient",
            details=str(exc),
        )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    output_schema=None
)
@_logged
@_on_com_thread
def validate_autosar(
    output_format: Annotated[ValidateOutputFormat, Field(
        description="Format for the validation result output. Accepted values: 'markdown', 'json'. Example: 'markdown'",
    )] = ValidateOutputFormat.MARKDOWN,
    timeout: Annotated[int, Field(
        description="Maximum number of seconds to wait for validation to complete. Example: 120",
    )] = 120,
) -> dict[str, Any]:
    """
    Validate the currently open SystemDesk project against AUTOSAR design rules.

    Does not modify the project.

    Preconditions: start_systemdesk must be called first; a project must be open.
    Returns error_count, warning_count, total_count, and a report (markdown or list of messages).

    Failure codes (error_type / condition):
    - permanent / SystemDesk not running (start_systemdesk not called)
    - permanent / no project currently open
    - transient / validation timed out
    - transient / validation call failed
    """
    params = ValidateAutosarInput(output_format=output_format, timeout=timeout)
    try:
        app = _get_app()

        active_project = app.ActiveProject
        if active_project is None:
            return _error(
                "No project is currently open. Call create_project or open a project first.",
                error_type="permanent",
            )

        # Validation runs on the dedicated COM apartment thread; the timeout
        # is enforced by the @_on_com_thread decorator.
        active_project.Validate.Do()

        messages = []
        for msg in active_project.Validate.ResultStructure.RuleResults:
            messages.append({
                "severity": msg.Severity,
                "rule": msg.RuleId,
                "description": msg.Message.Text,
                "element": msg.Entity.AUTOSARPathName if hasattr(msg.Entity, "AUTOSARPathName") else "N/A",
            })

        error_count = sum(1 for m in messages if m["severity"] == "Error")
        warning_count = sum(1 for m in messages if m["severity"] == "Warning")

        if params.output_format == ValidateOutputFormat.MARKDOWN:
            lines = [
                "# Validation Result",
                f"**Errors:** {error_count} | **Warnings:** {warning_count} | **Total:** {len(messages)}",
                "",
            ]
            if messages:
                lines.append("| Severity | Rule | Description | Element |")
                lines.append("|---|---|---|---|")
                for m in messages:
                    lines.append(
                        f"| {m['severity']} | {m['rule']} | {m['description']} | {m['element']} |"
                    )
            else:
                lines.append("✅ No validation issues found.")

            return _ok({
                "error_count": error_count,
                "warning_count": warning_count,
                "total_count": len(messages),
                "report": "\n".join(lines),
            })
        else:
            return _ok({
                "error_count": error_count,
                "warning_count": warning_count,
                "total_count": len(messages),
                "messages": messages,
            })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent")
    except Exception as exc:
        return _error(
            "Failed to validate the project.",
            error_type="transient",
            details=str(exc),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
