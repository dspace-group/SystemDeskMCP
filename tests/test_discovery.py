"""Tests for discovery behavior."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name

import pkgutil
import sys
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from systemdesk_mcp import mcp_app, tools as tool_package
from systemdesk_mcp.tools import load_tools


def test_discovery_imports_public_modules_in_sorted_order():
    # arrange
    modules = [pkgutil.ModuleInfo(None, name, False) for name in (
        "zebra", "_helpers", "alpha",
    )]

    # act
    with patch.object(tool_package.pkgutil, "iter_modules", return_value=modules), \
            patch.object(tool_package.importlib, "import_module") as import_module:
        load_tools()

    # assert
    assert [call.args[0] for call in import_module.call_args_list] == [
        "systemdesk_mcp.tools.alpha", "systemdesk_mcp.tools.zebra",
    ]


def test_discovery_propagates_import_errors():
    # arrange
    modules = [pkgutil.ModuleInfo(None, "broken", False)]

    # act / assert
    with patch.object(tool_package.pkgutil, "iter_modules", return_value=modules), \
            patch.object(tool_package.importlib, "import_module", side_effect=ImportError("broken")):
        with pytest.raises(ImportError, match="broken"):
            load_tools()


@pytest.mark.anyio
async def test_discovery_registers_new_module_once(tmp_path, monkeypatch):
    # arrange
    temporary_mcp = FastMCP("Discovery test")
    module_name = "systemdesk_mcp.tools.discovery_probe"
    module_file = tmp_path / "discovery_probe.py"
    module_file.write_text(
        "from systemdesk_mcp.mcp_app import mcp\n"
        "@mcp.tool\n"
        "def discovery_probe() -> str:\n"
        "    return 'discovered'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tool_package, "__path__", [str(tmp_path)])
    monkeypatch.setattr(mcp_app, "mcp", temporary_mcp)

    # act
    try:
        load_tools()
        registered = await temporary_mcp.list_tools()
        load_tools()
        repeated = await temporary_mcp.list_tools()

        # assert
        assert [tool.name for tool in registered] == ["discovery_probe"]
        assert [tool.name for tool in repeated] == ["discovery_probe"]
        assert repeated[0].fn is registered[0].fn
    finally:
        sys.modules.pop(module_name, None)
        monkeypatch.delattr(tool_package, "discovery_probe", raising=False)
