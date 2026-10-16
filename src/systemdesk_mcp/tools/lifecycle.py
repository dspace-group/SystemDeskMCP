"""SystemDesk application lifecycle tools."""
# pylint: disable=protected-access

from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp import runtime
from systemdesk_mcp.common import ErrorCode, _PermanentError, _error, _logged, _ok
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import _on_com_thread


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
    Must be called before using any other SystemDesk tool except `get_systemdesk_status`.
    
    Precondition: None, may be called at any time.
    Returns the ApplicationRootDir and the resolved ProgID on success.

    Failure codes (error_type / error_code: condition):
    - permanent / WIN32COM_NOT_INSTALLED: win32com not installed
    - permanent / PROGID_NOT_REGISTERED: SystemDesk ProgID not registered
    - permanent / ACCESS_DENIED: access denied
    - transient / BLOCKED_BY_DIALOG: SystemDesk blocked by open dialog
    - transient / START_FAILED: other failure while starting SystemDesk

    Hint: This tool does not provide a timeout parameter,
    because opening the COM connection has an inherent timeout.
    """
    try:
        import win32com.client # pylint: disable=import-outside-toplevel
    except ImportError:
        return _error(
            "win32com is not installed. Install pywin32 to use this tool.",
            error_type="permanent",
            error_code=ErrorCode.WIN32COM_NOT_INSTALLED,
        )

    prog_id = "SystemDesk.Application"

    # Check if already connected
    if runtime._application is not None:
        try:
            root_dir = runtime._application.ApplicationRootDir
            return _ok({
                "prog_id": prog_id,
                "application_root_dir": root_dir,
                "message": "SystemDesk is already running. Reusing existing COM connection.",
            })
        except Exception:  # pylint: disable=broad-exception-caught
            runtime._application = None  # stale connection, reconnect below

    try:
        runtime._application = win32com.client.Dispatch(prog_id)
        runtime._application.BatchMode = True  # suppresses popups for automation
        root_dir = runtime._application.ApplicationRootDir
        return _ok({
            "prog_id": prog_id,
            "application_root_dir": root_dir,
            "message": "SystemDesk started and COM connection established.",
        })
    except Exception as exc:  # pylint: disable=broad-exception-caught
        exc_str = str(exc)
        runtime._application = None

        if "blocked" in exc_str.lower() or "rejected" in exc_str.lower():
            message = (
                "SystemDesk blocked the automation request. "
                "A modal dialog (e.g. license warning, unsaved project prompt) may be open in SystemDesk. "
                "Please close any open dialogs in SystemDesk and try again."
            )
            error_type = "transient"
            error_code = ErrorCode.BLOCKED_BY_DIALOG
        elif "class not registered" in exc_str.lower() or "invalid class string" in exc_str.lower():
            message = (
                f"SystemDesk is not installed or the ProgID '{prog_id}' is not registered. "
                "Verify the SystemDesk installation."
            )
            error_type = "permanent"
            error_code = ErrorCode.PROGID_NOT_REGISTERED
        elif "access" in exc_str.lower() or "permission" in exc_str.lower():
            message = (
                "Access denied when connecting to SystemDesk. "
                "Try running the MCP server with elevated privileges."
            )
            error_type = "permanent"
            error_code = ErrorCode.ACCESS_DENIED
        else:
            message = (
                "Failed to start SystemDesk. "
                "Ensure SystemDesk is installed and no blocking dialog is open."
            )
            error_type = "transient"
            error_code = ErrorCode.START_FAILED

        return _error(message, error_type=error_type, error_code=error_code, details=exc_str)


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
        description=(
            "If True, the currently open project is saved before SystemDesk is closed. "
            "Example: True"
        ),
    )] = True,
) -> dict[str, Any]:
    """
    Close SystemDesk and release the COM connection.

    Optionally saves the open project before closing.
    After this call, start_systemdesk must be called again before using other tools.

    Precondition: start_systemdesk must be called first.
    Returns a confirmation message on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - transient / PROJECT_SAVE_FAILED: failed to save project before closing
    - transient / QUIT_FAILED: Quit call failed (returns immediately)
    """
    try:
        app = runtime._get_app()

        if save_project:
            try:
                active_project = app.ActiveProject
                if active_project is not None:
                    active_project.Save()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return _error(
                    "Failed to save project before closing SystemDesk.",
                    error_type="transient",
                    error_code=ErrorCode.PROJECT_SAVE_FAILED,
                    details=str(exc),
                )

        app.Quit()
        runtime._application = None
        return _ok({"message": "SystemDesk closed successfully."})

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error(
            "Failed to close SystemDesk.",
            error_type="transient",
            error_code=ErrorCode.QUIT_FAILED,
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

    if runtime._application is None:
        return _ok(
            {
                "running": False,
                "project_open": False,
                "project_name": None,
                "message": "SystemDesk is not running.",
            }
        )

    try:
        active_project = runtime._application.ActiveProject
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
    except Exception:  # pylint: disable=broad-exception-caught
        # Stale COM proxy or process closed unexpectedly.
        runtime._application = None
        return _ok(
            {
                "running": False,
                "project_open": False,
                "project_name": None,
                "message": "SystemDesk is not running.",
            }
        )
