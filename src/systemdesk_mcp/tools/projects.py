"""SystemDesk project creation and closing tools."""

import os
from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    ABSOLUTE_PATH_PATTERN,
    FILE_NAME_PATTERN,
    PermanentError,
    error_response,
    success_response,
)
from systemdesk_mcp.mcp_app import mcp
from systemdesk_mcp.runtime import get_app, logged, on_com_thread


@mcp.tool(
    title="Create Project",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    output_schema=None
)
@logged
@on_com_thread
def create_project(
    project_path: Annotated[str, Field(
        min_length=1,
        pattern=ABSOLUTE_PATH_PATTERN,
        description=(
            "Absolute local or UNC path to the directory where the project file will be created. "
            "Example: 'C:\\Projects\\MyECU'"
        ),
    )],
    project_name: Annotated[str, Field(
        min_length=1,
        pattern=FILE_NAME_PATTERN,
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
    - permanent / UNEXPECTED_ERROR: project creation or save failed
    """
    try:
        app = get_app()

        project_file = os.path.join(project_path, f"{project_name}.sdp")

        os.makedirs(project_path, exist_ok=True)

        project = app.CreateProject(project_file)
        project.Save()

        return success_response({
            "project_name": project_name,
            "project_file": project_file,
            "message": f"Project '{project_name}' created successfully.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            f"Failed to create project '{project_name}'.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )


@mcp.tool(
    title="Close Project",
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
    - permanent / UNEXPECTED_ERROR: close or save call failed
    """
    try:
        app = get_app()
        active_project = app.ActiveProject

        if active_project is None:
            return success_response({"message": "No project is currently open."})

        project_name = active_project.Name

        if save_project:
            active_project.Save()

        active_project.Close(False)

        return success_response({
            "project_name": project_name,
            "saved": save_project,
            "message": f"Project '{project_name}' closed successfully.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            "Failed to close the project.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )
