"""
Unit tests for systemdesk_mcp.server.

COM is mocked at the _application singleton level — no SystemDesk installation required.
"""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import os
from unittest.mock import MagicMock, patch

import pytest
import systemdesk_mcp.server as srv


@pytest.fixture(autouse=True)
def reset_com_singleton():
    saved = srv._application
    yield
    srv._application = saved


@pytest.fixture(autouse=True)
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


def test_import_autosar_wrong_extension(tmp_path):
    # arrange
    xml_file = tmp_path / "file.xml"
    xml_file.write_text("<data/>")

    # act
    result = srv.import_autosar_file(str(xml_file))

    # assert
    assert result["status"] == "error"
    assert "arxml" in result["message"].lower()


def test_import_autosar_file_not_found():
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
    import_export_file.Import.return_value = True
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
    import_export_file.Delete.assert_not_called()


def test_import_autosar_file_schema_error(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "invalid_model.arxml"
    arxml_file.write_text("<AUTOSAR><InvalidElement/></AUTOSAR>")
    active_project = MagicMock()
    import_export_file = MagicMock()
    import_export_file.Import.return_value = False
    active_project.ImportExportFiles.Add.return_value = import_export_file
    app.ActiveProject = active_project

    app.Messages.Elements = [MagicMock(), MagicMock()]
    msg = app.Messages.Elements[-1]
    msg.MessageIdentifier = 'Info(93,600,17)'
    msg.Severity = 'Info'
    msg.MessageText = 'Preparing'
    msg.Children.Elements = [MagicMock(), MagicMock()]
    msg.Children.Elements[0].Severity = 'Info'
    msg.Children.Elements[0].MessageText = 'Validating'
    msg.Children.Elements[0].Children.Elements = [MagicMock()]
    msg.Children.Elements[0].Children.Elements[0].Severity = 'Error'
    msg.Children.Elements[0].Children.Elements[0].MessageText = "Invalid element"
    msg.Children.Elements[0].Children.Elements[0].Children.Elements = []
    msg.Children.Elements[1].Severity = 'Error'
    msg.Children.Elements[1].MessageText = 'Import failed'
    msg.Children.Elements[1].Children.Elements = []

    # act
    result = srv.import_autosar_file(str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["details"] == "[Info] Preparing\n  [Info] Validating\n    [Error] Invalid element\n  [Error] Import failed"
    active_project.ImportExportFiles.Add.assert_called_once_with(str(arxml_file))
    import_export_file.Import.assert_called_once()
    import_export_file.Delete.assert_called_once()


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
    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Missing reference"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

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
    rule_result = MagicMock()
    rule_result.Severity = "Warning"
    rule_result.RuleId = "R210"
    rule_result.Message.Text = "Signal has no default value"
    rule_result.Entity.AUTOSARPathName = "/System/Signals/VehicleSpeed"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

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


def test_validate_autosar_with_child_messages_markdown(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False

    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Main information\nwith multiple lines"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"

    child_message1 = MagicMock()
    child_message1.Text = "Level 1 info"
    rule_result.Message.ChildMessages = [child_message1]

    child_message2 = MagicMock()
    child_message2.Text = "Level 2 info"
    child_message1.ChildMessages = [child_message2]

    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = srv.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert "Main information<br>with multiple lines<br>Level 1 info<br>Level 2 info" in result["report"]


def test_validate_autosar_with_child_messages_json(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False

    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Main information\nwith multiple lines"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"

    child_message1 = MagicMock()
    child_message1.Text = "Level 1 info"
    rule_result.Message.ChildMessages = [child_message1]

    child_message2 = MagicMock()
    child_message2.Text = "Level 2 info"
    child_message1.ChildMessages = [child_message2]

    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = srv.validate_autosar(output_format=srv.ValidateOutputFormat.JSON)

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert result["messages"] == [
        {
            "severity": "Error",
            "rule": "R101",
            "description": "Main information\nwith multiple lines",
            "description_details": [
                {
                    "text": "Level 1 info",
                    "children": [
                        {
                            "text": "Level 2 info",
                            "children": []
                        }
                    ]
                }
            ],
            "element": "/R344_invalid/SWC",
        }
    ]


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
