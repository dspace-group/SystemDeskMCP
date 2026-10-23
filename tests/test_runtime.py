"""Tests for shared COM worker execution and connection state helpers."""

import threading

import pytest

from systemdesk_mcp.common import ErrorCode, PermanentError
from systemdesk_mcp.runtime import get_active_project, on_com_thread


def test_on_com_thread_reuses_worker_separate_from_caller():
    """Independently wrapped calls run on the same non-calling thread."""
    # arrange
    caller_thread = threading.get_ident()

    def current_thread():
        return threading.get_ident()

    first_operation = on_com_thread(current_thread)
    second_operation = on_com_thread(current_thread)

    # act
    first_thread = first_operation()
    second_thread = second_operation()
    repeated_thread = first_operation()

    # assert
    assert first_thread == second_thread == repeated_thread
    assert first_thread != caller_thread


def test_get_active_project_returns_open_project(app):
    # act
    project = get_active_project()

    # assert
    assert project is app.ActiveProject


def test_get_active_project_no_project_open(app):
    # arrange
    app.ActiveProject = None

    # act / assert
    with pytest.raises(PermanentError, match="No project is currently open") as exc_info:
        get_active_project()
    assert exc_info.value.error_code == ErrorCode.NO_PROJECT_OPEN
