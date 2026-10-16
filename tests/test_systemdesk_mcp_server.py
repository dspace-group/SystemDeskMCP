"""
Unit tests for systemdesk_mcp.server.

COM is mocked at the _application singleton level — no SystemDesk installation required.
"""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import inspect
import json
import os
import runpy
import threading
import typing
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.tools import FunctionTool
from pydantic import TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
import systemdesk_mcp.server as srv
from systemdesk_mcp import common, runtime
from systemdesk_mcp.mcp_app import mcp


def _validate_args(tool, **kwargs):
    """Validate kwargs against a tool's signature like FastMCP does, without running it."""
    def stub(*_args, **_kwargs):
        return None
    stub.__signature__ = inspect.signature(tool)
    stub.__annotations__ = typing.get_type_hints(tool, include_extras=True)
    TypeAdapter(stub).validate_python(kwargs)


@pytest.fixture(autouse=True)
def reset_com_singleton():
    saved = runtime._application
    yield
    runtime._application = saved


@pytest.fixture(autouse=True)
def app():
    mock = MagicMock()
    runtime._application = mock
    return mock


def test_start_systemdesk_win32com_missing():
    # arrange
    runtime._application = None

    with patch.dict("sys.modules", {"win32com": None, "win32com.client": None}):
        # act
        result = srv.start_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "WIN32COM_NOT_INSTALLED"


def test_close_systemdesk_not_started():
    # arrange
    # Representative guard - same pattern across all tools.
    runtime._application = None

    # act
    result = srv.close_systemdesk()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


def test_get_systemdesk_status_not_running():
    # arrange
    runtime._application = None

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
    assert runtime._application is None


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
    assert runtime._application is None


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


def test_import_autosar_wrong_extension_rejected_by_schema(tmp_path):
    with pytest.raises(ValidationError):
        _validate_args(srv.import_autosar_file, file_path=str(tmp_path / "file.xml"))


_VALID_ABSOLUTE_DIRS = [
    "C:\\",
    "C:/",
    "c:\\Projects",
    "C:\\Projects\\My ECU",
    "C:/Projects/MyECU",
    "C:\\Projects/Mixed\\Separators",
    "\\\\server\\share",
    "\\\\server\\share\\Projects",
    "//server/share/Projects",
    "\\\\192.168.0.1\\share\\Projects",
    "\\\\?\\C:\\Very\\Long\\Path",
]

_INVALID_ABSOLUTE_DIRS = [
    "Projects",
    "Projects\\MyECU",
    ".\\Projects",
    "..\\Projects",
    "\\Projects",
    "/Projects",
    "C:",
    "C:Projects",
    "1:\\Projects",
    "\\\\server",
    "\\\\server\\",
    "\\\\\\share",
]


@pytest.mark.parametrize("project_path", _VALID_ABSOLUTE_DIRS)
def test_create_project_absolute_path_accepted_by_schema(project_path):
    _validate_args(srv.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize("project_path", _INVALID_ABSOLUTE_DIRS)
def test_create_project_non_absolute_path_rejected_by_schema(project_path):
    with pytest.raises(ValidationError):
        _validate_args(srv.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize(
    "file_path",
    [d.rstrip("\\/") + "\\file.arxml" for d in _VALID_ABSOLUTE_DIRS]
    + ["C:\\Projects\\file.ARXML", "C:\\Projects\\file.ArXmL", "C:\\Projects\\my.file.arxml"],
)
def test_import_autosar_absolute_path_accepted_by_schema(file_path):
    _validate_args(srv.import_autosar_file, file_path=file_path)


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
        _validate_args(srv.import_autosar_file, file_path=file_path)


@pytest.mark.parametrize("timeout", [0, -1, common._MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(timeout):
    with pytest.raises(ValidationError):
        _validate_args(srv.import_autosar_file, file_path="C:\\a.arxml", timeout=timeout)
    with pytest.raises(ValidationError):
        _validate_args(srv.validate_autosar, timeout=timeout)


@pytest.mark.parametrize("project_name", ["", "a/b", "a\\b", "a:b", "a*b", "a?b"])
def test_create_project_invalid_name_rejected_by_schema(project_name):
    with pytest.raises(ValidationError):
        _validate_args(
            srv.create_project, project_path="C:\\Projects", project_name=project_name
        )


def test_unknown_argument_rejected_by_schema():
    with pytest.raises(ValidationError):
        _validate_args(srv.close_systemdesk, save_projct=False)


def test_import_autosar_file_not_found():
    # act
    result = srv.import_autosar_file(file_path="C:\\nonexistent\\file.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FILE_NOT_FOUND"


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
    result = srv.import_autosar_file(file_path=str(arxml_file))

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
    result = srv.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "IMPORT_REJECTED"
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
    result = srv.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


def test_create_project_success(app, tmp_path):
    # arrange
    project_dir = tmp_path / "project_dir"
    project_name = "MyEcuArchitecture"
    expected_project_file = os.path.join(str(project_dir), f"{project_name}.sdp")
    created_project = MagicMock()
    app.CreateProject.return_value = created_project

    # act
    result = srv.create_project(project_path=str(project_dir), project_name=project_name)

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
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


@pytest.mark.anyio
async def test_tool_parameters_are_described_and_strict():
    tools = await srv.mcp.list_tools()
    assert tools

    for tool in tools:
        assert isinstance(tool, FunctionTool), (
            f"Tool '{tool.name}' is not a FunctionTool and cannot be inspected."
        )
        assert tool.parameters.get("additionalProperties") is False, (
            f"Tool '{tool.name}': schema must reject unknown arguments."
        )
        hints = typing.get_type_hints(tool.fn, include_extras=True)
        for name in inspect.signature(tool.fn).parameters:
            hint = hints.get(name)
            assert typing.get_origin(hint) is typing.Annotated and any(
                isinstance(m, FieldInfo) for m in hint.__metadata__
            ), (
                f"Tool '{tool.name}': parameter '{name}' must be Annotated[<type>, Field(...)]."
            )
        for name, prop in tool.parameters.get("properties", {}).items():
            assert "Example:" in prop.get("description", ""), (
                f"Tool '{tool.name}': parameter '{name}' needs a description with an example."
            )


@pytest.mark.anyio
async def test_server_runs_tool_when_field_constraint_is_met(app):
    # arrange
    app.ActiveProject.Validate.ResultStructure.RuleResults = []

    # act
    result = await srv.mcp.call_tool("validate_autosar", {"timeout": 1})

    # assert
    assert json.loads(result.content[0].text)["status"] == "success"
    app.ActiveProject.Validate.Do.assert_called_once()


@pytest.mark.anyio
async def test_server_enforces_field_constraint_before_tool_runs(app):
    # act
    # Called on the server without a client, so the server must be what rejects it.
    with pytest.raises(FastMCPValidationError):
        await srv.mcp.call_tool("validate_autosar", {"timeout": 0})

    # assert
    assert not app.mock_calls, "Tool body must not run when arguments are invalid."


@pytest.mark.anyio
async def test_tools_are_logged_and_on_com_thread():
    # Every wrapper produced by a decorator shares that decorator's inner code object.
    logged_code = common._logged(lambda: None).__code__
    com_thread_code = runtime._on_com_thread(lambda: None).__code__

    tools = await srv.mcp.list_tools()
    assert tools

    for tool in tools:
        assert isinstance(tool, FunctionTool), (
            f"Tool '{tool.name}' is not a FunctionTool and cannot be inspected."
        )
        outer = tool.fn
        assert outer.__code__ is logged_code, (
            f"Tool '{tool.name}' must be decorated with @_logged (outermost)."
        )
        inner = outer.__wrapped__
        assert inner.__code__ is com_thread_code, (
            f"Tool '{tool.name}' must be decorated with @_on_com_thread below @_logged."
        )
        assert not hasattr(inner.__wrapped__, "__wrapped__"), (
            f"Tool '{tool.name}' has unexpected extra decorators."
        )


@pytest.mark.anyio
async def test_server_registers_exactly_the_original_tools():
    # arrange
    expected_names = {
        "start_systemdesk", "close_systemdesk", "get_systemdesk_status",
        "create_project", "close_project", "import_autosar_file", "validate_autosar",
    }

    # act
    tools = await srv.mcp.list_tools()

    # assert
    assert srv.mcp is mcp
    assert len(tools) == len(expected_names)
    assert {tool.name for tool in tools} == expected_names
    assert all(tool.fn is getattr(srv, tool.name) for tool in tools)


def test_tool_groups_share_connection_and_com_worker(app, tmp_path):
    # arrange
    runtime._application = None
    worker_threads = []
    project = app.CreateProject.return_value
    project.Name = "SharedProject"
    project.Validate.ResultStructure.RuleResults = []
    app.ActiveProject = project
    arxml_file = tmp_path / "shared.arxml"
    arxml_file.write_text("<AUTOSAR/>")

    def record_thread(result):
        def call(*_args):
            worker_threads.append(threading.get_ident())
            return result
        return call

    app.CreateProject.side_effect = record_thread(project)
    project.ImportExportFiles.Add.return_value.Import.side_effect = record_thread(True)
    project.Validate.Do.side_effect = record_thread(None)
    app.Quit.side_effect = record_thread(None)

    # act
    with patch("win32com.client.Dispatch", side_effect=record_thread(app)):
        started = srv.start_systemdesk()
    created = srv.create_project(project_path=str(tmp_path), project_name="SharedProject")
    status = srv.get_systemdesk_status()
    imported = srv.import_autosar_file(file_path=str(arxml_file))
    validated = srv.validate_autosar()
    closed = srv.close_systemdesk(save_project=False)
    after_close = srv.validate_autosar()

    # assert
    assert all(result["status"] == "success" for result in (
        started, created, status, imported, validated, closed,
    ))
    assert status["project_name"] == "SharedProject"
    assert runtime._application is None
    assert after_close["status"] == "error"
    assert after_close["error_type"] == "permanent"
    assert after_close["error_code"] == "SYSTEMDESK_NOT_RUNNING"
    assert len(worker_threads) == 5
    assert len(set(worker_threads)) == 1
    assert worker_threads[0] != threading.get_ident()


def test_entry_point_uses_shared_mcp_without_duplicate_runtime():
    # arrange
    executor = runtime._com_executor

    # act
    with patch.object(mcp, "run") as run:
        entry_globals = runpy.run_path(srv.__file__, run_name="__main__")

    # assert
    run.assert_called_once_with(transport="stdio")
    assert entry_globals["mcp"] is srv.mcp
    assert entry_globals["start_systemdesk"] is srv.start_systemdesk
    assert runtime._com_executor is executor


def test_version_is_semver():
    parts = srv.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
