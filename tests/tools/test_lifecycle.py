"""Tests for lifecycle behavior."""
# pylint: disable=protected-access

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from systemdesk_mcp import runtime
from systemdesk_mcp.tools import lifecycle
from tests.helpers import validate_args


def test_start_systemdesk_win32com_missing():
    # arrange
    runtime._application = None

    with patch.dict("sys.modules", {"win32com": None, "win32com.client": None}):
        # act
        result = lifecycle.start_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "WIN32COM_NOT_INSTALLED"


def test_close_systemdesk_not_started():
    # arrange
    # Representative guard - same pattern across all tools.
    runtime._application = None

    # act
    result = lifecycle.close_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


def test_get_systemdesk_status_not_running():
    # arrange
    runtime._application = None

    # act
    result = lifecycle.get_systemdesk_status()

    # assert
    assert result["status"] == "success"
    assert result["running"] is False
    assert result["project_open"] is False
    assert result["project_name"] is None


def test_get_systemdesk_status_running_no_project(app):
    # arrange
    app.ActiveProject = None

    # act
    result = lifecycle.get_systemdesk_status()

    # assert
    assert result["status"] == "success"
    assert result["running"] is True
    assert result["project_open"] is False
    assert result["project_name"] is None


def test_get_systemdesk_status_running_with_project(app):
    # arrange
    active_project = MagicMock()
    active_project.Name = "MyProject"
    app.ActiveProject = active_project

    # act
    result = lifecycle.get_systemdesk_status()

    # assert
    assert result["status"] == "success"
    assert result["running"] is True
    assert result["project_open"] is True
    assert result["project_name"] == "MyProject"


def test_close_systemdesk_without_save(app):
    # act
    result = lifecycle.close_systemdesk(save_project=False)

    # assert
    assert result["status"] == "success"
    assert "closed successfully" in result["message"].lower()
    app.ActiveProject.Save.assert_not_called()
    app.Quit.assert_called_once()
    assert runtime._application is None


def test_close_systemdesk_with_save(app):
    # arrange
    active_project = MagicMock()
    app.ActiveProject = active_project

    # act
    result = lifecycle.close_systemdesk(save_project=True)

    # assert
    assert result["status"] == "success"
    assert "closed successfully" in result["message"].lower()
    active_project.Save.assert_called_once()
    app.Quit.assert_called_once()
    assert runtime._application is None


def test_unknown_argument_rejected_by_schema():
    with pytest.raises(ValidationError):
        validate_args(lifecycle.close_systemdesk, save_projct=False)
