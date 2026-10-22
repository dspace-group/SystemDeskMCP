"""Tests for projects behavior."""

import os
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp import runtime
from systemdesk_mcp.tools import projects
from tests.helpers import INVALID_ABSOLUTE_DIRS, VALID_ABSOLUTE_DIRS, validate_args


def test_close_project_with_save(app):
    # arrange
    active_project = MagicMock()
    active_project.Name = "MyProject"
    app.ActiveProject = active_project

    # act
    result = projects.close_project(save_project=True)

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyProject"
    assert result["saved"] is True
    assert "closed successfully" in result["message"].lower()
    active_project.Save.assert_called_once()
    active_project.Close.assert_called_once_with(False)


def test_close_project_without_save(app):
    # arrange
    active_project = MagicMock()
    active_project.Name = "MyProject"
    app.ActiveProject = active_project

    # act
    result = projects.close_project(save_project=False)

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyProject"
    assert result["saved"] is False
    assert "closed successfully" in result["message"].lower()
    active_project.Save.assert_not_called()
    active_project.Close.assert_called_once_with(False)


def test_close_project_not_started():
    # arrange
    # Representative guard - same pattern across all tools.
    runtime._application = None  # pylint: disable=protected-access

    # act
    result = projects.close_project()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


def test_create_project_success(app, tmp_path):
    # arrange
    project_dir = tmp_path / "project_dir"
    project_name = "MyEcuArchitecture"
    expected_project_file = os.path.join(str(project_dir), f"{project_name}.sdp")
    created_project = MagicMock()
    app.CreateProject.return_value = created_project

    # act
    result = projects.create_project(project_path=str(project_dir), project_name=project_name)

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == project_name
    assert result["project_file"] == expected_project_file
    assert "created successfully" in result["message"].lower()
    assert project_dir.is_dir()
    app.CreateProject.assert_called_once_with(expected_project_file)
    created_project.Save.assert_called_once()


@pytest.mark.parametrize("project_path", VALID_ABSOLUTE_DIRS)
def test_create_project_absolute_path_accepted_by_schema(project_path):
    validate_args(projects.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize("project_path", INVALID_ABSOLUTE_DIRS)
def test_create_project_non_absolute_path_rejected_by_schema(project_path):
    with pytest.raises(ValidationError):
        validate_args(projects.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize("project_name", ["", "a/b", "a\\b", "a:b", "a*b", "a?b"])
def test_create_project_invalid_name_rejected_by_schema(project_name):
    with pytest.raises(ValidationError):
        validate_args(
            projects.create_project, project_path="C:\\Projects", project_name=project_name
        )


def test_open_project_success(app, tmp_path):
    # arrange
    project_file = tmp_path / "MyEcu.sdp"
    project_file.write_text("")
    app.ActiveProject = None
    opened_project = MagicMock()
    opened_project.Name = "MyEcuArchitecture"

    def open_project(_path):
        app.ActiveProject = opened_project

    app.OpenProject.side_effect = open_project

    # act
    result = projects.open_project(project_file=str(project_file))

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyEcuArchitecture"
    assert result["project_file"] == str(project_file)
    assert "opened successfully" in result["message"].lower()
    app.OpenProject.assert_called_once_with(str(project_file))


def test_open_project_discards_active_project(app, tmp_path):
    # arrange
    project_file = tmp_path / "MyEcu.sdp"
    project_file.write_text("")
    active_project = MagicMock()
    active_project.Repository = str(tmp_path / "Other.sdp")
    app.ActiveProject = active_project
    opened_project = MagicMock()
    opened_project.Name = "MyEcu"

    def open_project(_path):
        app.ActiveProject = opened_project

    app.OpenProject.side_effect = open_project

    # act
    result = projects.open_project(project_file=str(project_file))

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyEcu"
    active_project.Save.assert_not_called()
    active_project.Close.assert_called_once_with(False)
    app.OpenProject.assert_called_once_with(str(project_file))


def test_open_project_already_open(app, tmp_path):
    # arrange
    project_file = tmp_path / "MyEcu.sdp"
    project_file.write_text("")
    active_project = MagicMock()
    active_project.Name = "MyEcu"
    active_project.Repository = str(project_file).upper()
    app.ActiveProject = active_project

    # act
    result = projects.open_project(project_file=str(project_file))

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyEcu"
    assert result["project_file"] == str(project_file)
    assert "already open" in result["message"].lower()
    active_project.Close.assert_not_called()
    app.OpenProject.assert_not_called()


def test_open_project_file_not_found(app, tmp_path):
    # arrange
    active_project = app.ActiveProject

    # act
    result = projects.open_project(project_file=str(tmp_path / "Missing.sdp"))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FILE_NOT_FOUND"
    active_project.Close.assert_not_called()
    app.OpenProject.assert_not_called()


def test_open_project_com_failure(app, tmp_path):
    # arrange
    project_file = tmp_path / "MyEcu.sdp"
    project_file.write_text("")
    app.ActiveProject = None
    app.OpenProject.side_effect = Exception("COM error")

    # act
    result = projects.open_project(project_file=str(project_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["details"] == "COM error"


@pytest.mark.parametrize(
    "project_file",
    [d.rstrip("\\/") + "\\MyECU.sdp" for d in VALID_ABSOLUTE_DIRS]
    + ["C:\\Projects\\MyECU.SDP", "C:\\Projects\\MyECU.SdP", "C:\\Projects\\my.ecu.sdp"],
)
def test_open_project_absolute_path_accepted_by_schema(project_file):
    validate_args(projects.open_project, project_file=project_file)


@pytest.mark.parametrize(
    "project_file",
    [
        "MyECU.sdp",
        "Projects\\MyECU.sdp",
        ".\\MyECU.sdp",
        "..\\Projects\\MyECU.sdp",
        "\\Projects\\MyECU.sdp",
        "/Projects/MyECU.sdp",
        "C:MyECU.sdp",
        "1:\\Projects\\MyECU.sdp",
        "\\\\\\share\\MyECU.sdp",
        "C:\\Projects\\MyECU.xml",
        "C:\\Projects\\MyECU.sdpx",
        "C:\\Projects\\MyECU.sdp.bak",
        "C:\\Projects\\MyECUsdp",
    ],
)
def test_open_project_invalid_path_rejected_by_schema(project_file):
    with pytest.raises(ValidationError):
        validate_args(projects.open_project, project_file=project_file)
