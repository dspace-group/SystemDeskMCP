"""Shared fixtures for tests that do not require a SystemDesk installation."""

from unittest.mock import MagicMock

import pytest

from systemdesk_mcp import runtime


@pytest.fixture(autouse=True)
def app(monkeypatch):
    """Install a mock COM application and restore the previous state on teardown."""
    mock = MagicMock()
    monkeypatch.setattr(runtime, "_application", mock)
    monkeypatch.setattr(runtime, "_last_vecu", None)
    monkeypatch.setattr(runtime, "_vecu_options", None)
    return mock
