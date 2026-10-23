"""SystemDesk project creation, opening, and closing tools."""

import os
from typing import Annotated, Any

from pydantic import Field

from systemdesk_mcp.common import (
    ErrorCode,
    ABSOLUTE_PATH_PATTERN,
    FILE_NAME_PATTERN,
    SDP_PATTERN,
    PermanentError,
    TimeoutSeconds,
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
    which can lead to data loss if the current project is not saved;
    call close_project(save_project=true) first to keep changes.

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
        description="If true, saves the project before closing it. Example: true",
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


@mcp.tool(
    title="Open Project",
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
def open_project(
    project_file: Annotated[str, Field(
        min_length=1,
        pattern=SDP_PATTERN,
        description=(
            "Absolute local or UNC path to an existing SystemDesk project file (.sdp). "
            "Example: 'C:\\Projects\\MyECU\\MyECU.sdp'"
        ),
    )],
    timeout: Annotated[TimeoutSeconds, Field(  # pylint: disable=unused-argument  # used by @on_com_thread
        description="Maximum number of seconds to wait for the project to open. Example: 60",
    )] = 60,
) -> dict[str, Any]:
    """
    Open an existing SystemDesk project file (.sdp).

    Automatically closes any currently open project without saving before opening,
    which can lead to data loss if the current project is not saved;
    call close_project(save_project=true) first to keep changes.
    If project_file is already open, this is a no-op that returns success
    without reloading, so unsaved changes are kept.

    Preconditions: start_systemdesk must be called first;
    project_file must point to an existing .sdp file.
    Returns project_name and project_file path on success.

    Failure codes (error_type / error_code: condition):
    - permanent / SYSTEMDESK_NOT_RUNNING: start_systemdesk not called
    - permanent / FILE_NOT_FOUND: project file not found
    - transient / TIMEOUT: opening timed out
    - permanent / UNEXPECTED_ERROR: close or open call failed
    """
    try:
        app = get_app()

        if not os.path.isfile(project_file):
            raise PermanentError(
                f"File not found: '{project_file}'. "
                "Provide an absolute path to an existing .sdp file.",
                error_code=ErrorCode.FILE_NOT_FOUND,
            )

        active_project = app.ActiveProject
        if active_project is not None:
            active_file = os.path.normcase(os.path.abspath(active_project.Repository))
            if active_file == os.path.normcase(os.path.abspath(project_file)):
                project_name = active_project.Name
                return success_response({
                    "project_name": project_name,
                    "project_file": project_file,
                    "message": f"Project '{project_name}' is already open.",
                })
            active_project.Close(False)

        app.OpenProject(project_file)
        project_name = app.ActiveProject.Name

        return success_response({
            "project_name": project_name,
            "project_file": project_file,
            "message": f"Project '{project_name}' opened successfully.",
        })

    except PermanentError as exc:
        return error_response(str(exc), error_type="permanent", error_code=exc.error_code)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error_response(
            f"Failed to open project '{project_file}'.",
            error_type="permanent",
            error_code=ErrorCode.UNEXPECTED_ERROR,
            details=str(exc),
        )
