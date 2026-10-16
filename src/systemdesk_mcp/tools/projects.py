"""SystemDesk project creation and closing tools."""

import os
from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    _ABSOLUTE_PATH_PATTERN,
    _FILE_NAME_PATTERN,
    _PermanentError,
    _error,
    _logged,
    _ok,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import _get_app, _on_com_thread


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
        min_length=1,
        pattern=_ABSOLUTE_PATH_PATTERN,
        description=(
            "Absolute local or UNC path to the directory where the project file will be created. "
            "Example: 'C:\\Projects\\MyECU'"
        ),
    )],
    project_name: Annotated[str, Field(
        min_length=1,
        pattern=_FILE_NAME_PATTERN,
        description=(
            "Name of the new SystemDesk project (without file extension). "
            "Must not contain path separators or any of <>:\"/\\|?*. "
            "Example: 'MyECU_Architecture'"
        ),
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

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - transient / PROJECT_CREATION_FAILED: project creation or save failed
    """
    try:
        app = _get_app()

        project_file = os.path.join(project_path, f"{project_name}.sdp")

        os.makedirs(project_path, exist_ok=True)

        project = app.CreateProject(project_file)
        project.Save()

        return _ok({
            "project_name": project_name,
            "project_file": project_file,
            "message": f"Project '{project_name}' created successfully.",
        })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error(
            f"Failed to create project '{project_name}'.",
            error_type="transient",
            error_code=ErrorCode.PROJECT_CREATION_FAILED,
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
    If no project is open, this is a no-op and returns success with only a message
    (no project_name or saved flag).

    Precondition: start_systemdesk must be called first.
    Returns project_name and saved flag on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - transient / PROJECT_CLOSE_FAILED: close or save call failed
    """
    try:
        app = _get_app()
        active_project = app.ActiveProject

        if active_project is None:
            return _ok({"message": "No project is currently open."})

        project_name = active_project.Name

        if save_project:
            active_project.Save()

        active_project.Close(False)

        return _ok({
            "project_name": project_name,
            "saved": save_project,
            "message": f"Project '{project_name}' closed successfully.",
        })

    except _PermanentError as exc:
        return _error(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error(
            "Failed to close the project.",
            error_type="transient",
            error_code=ErrorCode.PROJECT_CLOSE_FAILED,
            details=str(exc),
        )
