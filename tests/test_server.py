"""Tests for server behavior."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import inspect
import json
import threading
import typing
from unittest.mock import patch

import pytest
from pydantic.fields import FieldInfo
from fastmcp.exceptions import ValidationError as FastMCPValidationError

import systemdesk_mcp.server as srv
from systemdesk_mcp import common, runtime
from systemdesk_mcp.tools import autosar, lifecycle, projects
from tests.helpers import registered_function_tools


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
    logged_code = common.logged(lambda: None).__code__
    com_thread_code = runtime.on_com_thread(lambda: None).__code__

    tools = await registered_function_tools(srv.mcp)

    for tool in tools:
        outer = tool.fn
        assert outer.__code__ is logged_code, (
            f"Tool '{tool.name}' must be decorated with @logged (outermost)."
        )
        inner = outer.__wrapped__
        assert inner.__code__ is com_thread_code, (
            f"Tool '{tool.name}' must be decorated with @on_com_thread below @logged."
        )
        assert not hasattr(inner.__wrapped__, "__wrapped__"), (
            f"Tool '{tool.name}' has unexpected extra decorators."
        )


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
        started = lifecycle.start_systemdesk()
    created = projects.create_project(project_path=str(tmp_path), project_name="SharedProject")
    status = lifecycle.get_systemdesk_status()
    imported = autosar.import_autosar_file(file_path=str(arxml_file))
    validated = autosar.validate_autosar()
    closed = lifecycle.close_systemdesk(save_project=False)
    after_close = autosar.validate_autosar()

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


def test_version_is_semver():
    parts = srv.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


@pytest.mark.anyio
async def test_tool_parameters_are_described_and_strict():
    tools = await registered_function_tools(srv.mcp)

    for tool in tools:
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
