"""
Unit tests for systemdesk_mcp_server.

COM is mocked at the _application singleton level — no SystemDesk installation required.
"""

import os
import pytest
from unittest.mock import MagicMock, patch
import src.systemdesk_mcp_server as srv


@pytest.fixture(autouse=True)
def reset_com_singleton():
    saved = srv._application
    yield
    srv._application = saved


@pytest.fixture
def app():
    mock = MagicMock()
    srv._application = mock
    return mock


def test_start_systemdesk_win32com_missing():
    # arrange
    srv._application = None

    with patch.dict("sys.modules", {"win32com": None, "win32com.client": None}):
        # act
        result = srv.start_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"


def test_close_systemdesk_not_started():
    # arrange
    # Representative guard - same pattern across all tools.
    srv._application = None

    # act
    result = srv.close_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"


def test_get_systemdesk_status_not_running():
    # arrange
    srv._application = None

    # act
    result = srv.get_systemdesk_status()

    # assert
    assert result["status"] == "success"
    assert result["running"] is False
    assert result["project_open"] is False
    assert result["project_name"] is None


def test_get_systemdesk_status_running_no_project(app):
    # arrange
    app.ActiveProject = None

    # act
    result = srv.get_systemdesk_status()

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
    result = srv.get_systemdesk_status()

    # assert
    assert result["status"] == "success"
    assert result["running"] is True
    assert result["project_open"] is True
    assert result["project_name"] == "MyProject"


def test_close_systemdesk_without_save(app):
    # act
    result = srv.close_systemdesk(save_project=False)

    # assert
    assert result["status"] == "success"
    assert "closed successfully" in result["message"].lower()
    app.ActiveProject.Save.assert_not_called()
    app.Quit.assert_called_once()
    assert srv._application is None


def test_close_systemdesk_with_save(app):
    # arrange
    active_project = MagicMock()
    app.ActiveProject = active_project

    # act
    result = srv.close_systemdesk(save_project=True)

    # assert
    assert result["status"] == "success"
    assert "closed successfully" in result["message"].lower()
    active_project.Save.assert_called_once()
    app.Quit.assert_called_once()
    assert srv._application is None


def test_close_project_with_save(app):
    # arrange
    active_project = MagicMock()
    active_project.Name = "MyProject"
    app.ActiveProject = active_project

    # act
    result = srv.close_project(save_project=True)

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
    result = srv.close_project(save_project=False)

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == "MyProject"
    assert result["saved"] is False
    assert "closed successfully" in result["message"].lower()
    active_project.Save.assert_not_called()
    active_project.Close.assert_called_once_with(False)


def test_import_autosar_wrong_extension(app, tmp_path):
    # arrange
    xml_file = tmp_path / "file.xml"
    xml_file.write_text("<data/>")

    # act
    result = srv.import_autosar_file(str(xml_file))

    # assert
    assert result["status"] == "error"
    assert "arxml" in result["message"].lower()


def test_import_autosar_file_not_found(app):
    # act
    result = srv.import_autosar_file("C:\\nonexistent\\file.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"


def test_import_autosar_file_success(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "valid_model.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    active_project = MagicMock()
    import_export_file = MagicMock()
    active_project.ImportExportFiles.Add.return_value = import_export_file
    app.ActiveProject = active_project

    # act
    result = srv.import_autosar_file(str(arxml_file))

    # assert
    assert result["status"] == "success"
    assert result["imported_file"] == str(arxml_file)
    assert "imported successfully" in result["message"].lower()
    active_project.ImportExportFiles.Add.assert_called_once_with(str(arxml_file))
    import_export_file.Import.assert_called_once()


def test_import_autosar_no_project_open(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "sample.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    app.ActiveProject = None

    # act
    result = srv.import_autosar_file(str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert "no project is currently open" in result["message"].lower()


def test_create_project_success(app, tmp_path):
    # arrange
    project_dir = tmp_path / "project_dir"
    project_name = "MyEcuArchitecture"
    expected_project_file = os.path.join(str(project_dir), f"{project_name}.sdp")
    created_project = MagicMock()
    app.CreateProject.return_value = created_project

    # act
    result = srv.create_project(str(project_dir), project_name)

    # assert
    assert result["status"] == "success"
    assert result["project_name"] == project_name
    assert result["project_file"] == expected_project_file
    assert "created successfully" in result["message"].lower()
    assert project_dir.is_dir()
    app.CreateProject.assert_called_once_with(expected_project_file)
    created_project.Save.assert_called_once()


def test_validate_autosar_with_error(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False
    msg = MagicMock()
    msg.Severity = "Error"
    msg.RuleId = "R101"
    msg.Message.Text = "Missing reference"
    msg.Entity.AUTOSARPathName = "/R344_invalid/SWC"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [msg]

    # act
    result = srv.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert result["warning_count"] == 0
    assert result["total_count"] == 1
    assert "R101" in result["report"]
    assert "Missing reference" in result["report"]
    assert "/R344_invalid/SWC" in result["report"]


def test_validate_autosar_with_warning(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = True
    msg = MagicMock()
    msg.Severity = "Warning"
    msg.RuleId = "R210"
    msg.Message.Text = "Signal has no default value"
    msg.Entity.AUTOSARPathName = "/System/Signals/VehicleSpeed"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [msg]

    # act
    result = srv.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert result["warning_count"] == 1
    assert result["total_count"] == 1
    assert "R210" in result["report"]
    assert "Signal has no default value" in result["report"]
    assert "/System/Signals/VehicleSpeed" in result["report"]


def test_validate_autosar_with_no_results(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = True
    app.ActiveProject.Validate.ResultStructure.RuleResults = []

    # act
    result = srv.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert result["warning_count"] == 0
    assert result["total_count"] == 0
    assert "No validation issues found" in result["report"]


def test_validate_autosar_no_project_open(app):
    # arrange
    app.ActiveProject = None

    # act
    result = srv.validate_autosar()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert "no project is currently open" in result["message"].lower()


def test_version_is_semver():
    parts = srv.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
