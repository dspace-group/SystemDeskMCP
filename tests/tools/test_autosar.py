"""Tests for autosar behavior."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import autosar
from systemdesk_mcp import common
from tests.helpers import VALID_ABSOLUTE_DIRS, validate_args


def test_import_autosar_file_not_found():
    # act
    result = autosar.import_autosar_file(file_path="C:\\nonexistent\\file.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FILE_NOT_FOUND"


def test_import_autosar_file_success(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "valid_model.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    active_project = MagicMock()
    autosar_project_file = MagicMock()
    active_project.AutosarProjectFiles.GetOrAdd.return_value = autosar_project_file
    active_project.Serializer.ReadAutosarProjectFiles.return_value = True
    app.ActiveProject = active_project

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "success"
    assert result["imported_file"] == str(arxml_file)
    assert "imported successfully" in result["message"].lower()
    active_project.AutosarProjectFiles.GetOrAdd.assert_called_once_with(str(arxml_file))
    active_project.Serializer.ReadAutosarProjectFiles.assert_called_once_with([autosar_project_file])
    autosar_project_file.Delete.assert_not_called()


def test_import_autosar_no_project_open(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "sample.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    app.ActiveProject = None

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


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
    result = autosar.validate_autosar()

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
    result = autosar.validate_autosar()

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
    result = autosar.validate_autosar()

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
    result = autosar.validate_autosar(output_format=autosar.ValidateOutputFormat.JSON)

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
    result = autosar.validate_autosar()

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
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


def test_import_autosar_file_schema_error(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "invalid_model.arxml"
    arxml_file.write_text("<AUTOSAR><InvalidElement/></AUTOSAR>")
    active_project = MagicMock()
    autosar_project_file = MagicMock()
    active_project.AutosarProjectFiles.GetOrAdd.return_value = autosar_project_file
    active_project.Serializer.ReadAutosarProjectFiles.return_value = False
    app.ActiveProject = active_project

    app.Messages.Elements = [MagicMock(), MagicMock()]
    msg = app.Messages.Elements[-1]
    msg.MessageIdentifier = 'Info(93,600,124)'
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
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "IMPORT_REJECTED"
    assert result["details"] == "[Info] Preparing\n  [Info] Validating\n    [Error] Invalid element\n  [Error] Import failed"
    active_project.AutosarProjectFiles.GetOrAdd.assert_called_once_with(str(arxml_file))
    active_project.Serializer.ReadAutosarProjectFiles.assert_called_once_with([autosar_project_file])
    autosar_project_file.Delete.assert_called_once()


def test_import_autosar_file_error_details_skip_trace_messages(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "invalid_model.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    active_project = MagicMock()
    active_project.Serializer.ReadAutosarProjectFiles.return_value = False
    app.ActiveProject = active_project

    trace = MagicMock(Severity='Trace', MessageText='Tracing')
    error = MagicMock(Severity='Error', MessageText='Import failed')
    error.Children.Elements = [trace]
    msg = MagicMock(MessageIdentifier='Info(93,600,124)', Severity='Info', MessageText='Preparing')
    msg.Children.Elements = [trace, error, trace]
    app.Messages.Elements = [MagicMock(), msg]

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["error_code"] == "IMPORT_REJECTED"
    assert result["details"] == "[Info] Preparing\n  [Error] Import failed"


def test_import_autosar_wrong_extension_rejected_by_schema(tmp_path):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path=str(tmp_path / "file.xml"))


@pytest.mark.parametrize(
    "file_path",
    [d.rstrip("\\/") + "\\file.arxml" for d in VALID_ABSOLUTE_DIRS]
    + ["C:\\Projects\\file.ARXML", "C:\\Projects\\file.ArXmL", "C:\\Projects\\my.file.arxml"],
)
def test_import_autosar_absolute_path_accepted_by_schema(file_path):
    validate_args(autosar.import_autosar_file, file_path=file_path)


@pytest.mark.parametrize(
    "file_path",
    [
        "file.arxml",
        "Projects\\file.arxml",
        ".\\file.arxml",
        "..\\Projects\\file.arxml",
        "\\Projects\\file.arxml",
        "/Projects/file.arxml",
        "C:file.arxml",
        "1:\\Projects\\file.arxml",
        "\\\\\\share\\file.arxml",
        "C:\\Projects\\file.xml",
        "C:\\Projects\\file.arxml.bak",
        "C:\\Projects\\filearxml",
    ],
)
def test_import_autosar_invalid_path_rejected_by_schema(file_path):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path=file_path)


@pytest.mark.parametrize("timeout", [0, -1, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(timeout):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path="C:\\a.arxml", timeout=timeout)
    with pytest.raises(ValidationError):
        validate_args(autosar.validate_autosar, timeout=timeout)


def test_export_imported_autosar_file_success(app):
    # arrange
    file_path = "C:\\Projects\\model.arxml"
    active_project = MagicMock()
    autosar_project_file = MagicMock()
    active_project.AutosarProjectFiles.Item.return_value = autosar_project_file
    active_project.Serializer.WriteAutosarProjectFiles.return_value = True
    app.ActiveProject = active_project

    # act
    result = autosar.export_imported_autosar_file(file_path=file_path)

    # assert
    assert result["status"] == "success"
    assert result["exported_file"] == file_path
    assert "exported successfully" in result["message"].lower()
    active_project.AutosarProjectFiles.Item.assert_called_once_with(file_path)
    active_project.Serializer.WriteAutosarProjectFiles.assert_called_once_with([autosar_project_file])


def test_export_imported_autosar_file_not_imported(app):
    # arrange
    active_project = MagicMock()
    active_project.AutosarProjectFiles.Item.return_value = None
    app.ActiveProject = active_project

    # act
    result = autosar.export_imported_autosar_file(file_path="C:\\Projects\\model.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_ARGUMENTS"
    active_project.Serializer.WriteAutosarProjectFiles.assert_not_called()


def test_export_imported_autosar_file_write_failed(app):
    # arrange
    active_project = MagicMock()
    active_project.Serializer.WriteAutosarProjectFiles.return_value = False
    app.ActiveProject = active_project

    msg = MagicMock()
    msg.MessageIdentifier = 'Info(93,600,14)'
    msg.Severity = 'Info'
    msg.MessageText = 'Preparing'
    msg.Children.Elements = [MagicMock()]
    msg.Children.Elements[0].Severity = 'Error'
    msg.Children.Elements[0].MessageText = 'Write failed'
    msg.Children.Elements[0].Children.Elements = []
    app.Messages.Elements = [MagicMock(), msg]

    # act
    result = autosar.export_imported_autosar_file(file_path="C:\\Projects\\model.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_OPERATION_FAILED"
    assert result["details"] == "[Info] Preparing\n  [Error] Write failed"


def test_export_imported_autosar_file_unexpected_error(app):
    # arrange
    active_project = MagicMock()
    active_project.Serializer.WriteAutosarProjectFiles.side_effect = RuntimeError("write crashed")
    app.ActiveProject = active_project

    # act
    result = autosar.export_imported_autosar_file(file_path="C:\\Projects\\model.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["details"] == "write crashed"


def test_export_imported_autosar_file_wrong_extension_rejected_by_schema():
    with pytest.raises(ValidationError):
        validate_args(autosar.export_imported_autosar_file, file_path="C:\\Projects\\file.xml")
