"""Shared schema validation helpers for tests."""

import inspect
import typing

from pydantic import TypeAdapter


def validate_args(tool, **kwargs):
    """Validate tool arguments without invoking the tool body."""
    def stub(*_args, **_kwargs):
        return None
    stub.__signature__ = inspect.signature(tool)
    stub.__annotations__ = typing.get_type_hints(tool, include_extras=True)
    TypeAdapter(stub).validate_python(kwargs)


VALID_ABSOLUTE_DIRS = [
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

INVALID_ABSOLUTE_DIRS = [
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
