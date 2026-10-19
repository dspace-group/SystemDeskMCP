"""Tests for schemas behavior."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import inspect
import typing

import pytest
from pydantic import ValidationError
from pydantic.fields import FieldInfo

import systemdesk_mcp.server as srv
from systemdesk_mcp import common
from systemdesk_mcp.tools import autosar, lifecycle, projects
from tests.helpers import registered_function_tools, validate_args


def test_import_autosar_wrong_extension_rejected_by_schema(tmp_path):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path=str(tmp_path / "file.xml"))


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
    validate_args(projects.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize("project_path", _INVALID_ABSOLUTE_DIRS)
def test_create_project_non_absolute_path_rejected_by_schema(project_path):
    with pytest.raises(ValidationError):
        validate_args(projects.create_project, project_path=project_path, project_name="MyECU")


@pytest.mark.parametrize(
    "file_path",
    [d.rstrip("\\/") + "\\file.arxml" for d in _VALID_ABSOLUTE_DIRS]
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


@pytest.mark.parametrize("project_name", ["", "a/b", "a\\b", "a:b", "a*b", "a?b"])
def test_create_project_invalid_name_rejected_by_schema(project_name):
    with pytest.raises(ValidationError):
        validate_args(
            projects.create_project, project_path="C:\\Projects", project_name=project_name
        )


def test_unknown_argument_rejected_by_schema():
    with pytest.raises(ValidationError):
        validate_args(lifecycle.close_systemdesk, save_projct=False)


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
