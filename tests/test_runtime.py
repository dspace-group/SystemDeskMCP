"""Tests for shared COM worker execution."""

import threading

from systemdesk_mcp.runtime import on_com_thread


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
