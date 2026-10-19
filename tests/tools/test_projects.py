"""Tests for projects behavior."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import os
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

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
