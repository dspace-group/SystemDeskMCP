"""Tests for vecu_export behavior."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import vecu_export
from systemdesk_mcp import common, runtime
from tests.helpers import INVALID_ABSOLUTE_DIRS, VALID_ABSOLUTE_DIRS, validate_args

PROJECT_FILE = os.path.join("C:\\", "Projects", "MyECU", "MyECU.sdp")


def _vecu(name="MyVEcu", build_script=None, default_build_script="C:\\Build\\default_build.py"):
    v_ecu = MagicMock()
    v_ecu.Name = name
    v_ecu.BuildScript.Evaluate.return_value = build_script
    v_ecu.DefaultBuildScriptFile.Evaluate.return_value = default_build_script
    export_settings = SimpleNamespace(Result=_build_result())
    v_ecu.GetNewExportFmuSettings.return_value = export_settings
    return v_ecu


def _build_result(*errors):
    return SimpleNamespace(AnyError=bool(errors), Elements=list(errors))


def _build_message(identifier, text="", severity="Error", children=()):
    return SimpleNamespace(
        MessageIdentifier=identifier,
        MessageText=text,
        Severity=severity,
        Children=SimpleNamespace(Elements=list(children)),
    )


def _bsw_result(*texts):
    if not texts:
        return SimpleNamespace(ErrorMessages=SimpleNamespace(Count=0, Elements=[]))
    return SimpleNamespace(ErrorMessages=SimpleNamespace(
        Count=len(texts), Elements=[SimpleNamespace(MessageText=text) for text in texts],
    ))


def _set_vecus(app, *vecus):
    by_name = {v_ecu.Name: v_ecu for v_ecu in vecus}

    def _item(name):
        if name not in by_name:
            raise RuntimeError("not found")
        return by_name[name]

    app.ActiveProject.VEcus.Item.side_effect = _item
    app.ActiveProject.File = PROJECT_FILE


@pytest.fixture(name="no_files")
def _no_files(monkeypatch):
    """Pretend no build script files exist on disk."""
    monkeypatch.setattr(vecu_export.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(vecu_export.os.path, "exists", lambda _path: False)
    removed = []
    monkeypatch.setattr(vecu_export.os, "remove", removed.append)
    return removed


TOOLS = [vecu_export.generate_vecu_bsw, vecu_export.export_vecu_fmu]


@pytest.mark.parametrize("tool", TOOLS)
def test_not_started(tool):
    # arrange
    runtime._application = None  # pylint: disable=protected-access

    # act
    result = tool(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


@pytest.mark.parametrize("tool", TOOLS)
def test_no_project_open(app, tool):
    # arrange
    app.ActiveProject = None

    # act
    result = tool(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"


@pytest.mark.parametrize("tool", TOOLS)
def test_vecu_not_found(app, tool):
    # arrange
    _set_vecus(app, _vecu("OtherVEcu"))

    # act
    result = tool(vecu_name="Missing")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "'Missing'" in result["message"]
    assert "get_or_create_vecu" in result["message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_vecu_name_is_required_by_schema(tool):
    with pytest.raises(ValidationError):
        validate_args(tool)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("vecu_name", ["", "1Invalid", "My VEcu", "/MyVEcu", "A" * 129])
def test_invalid_vecu_name_rejected_by_schema(tool, vecu_name):
    with pytest.raises(ValidationError):
        validate_args(tool, vecu_name=vecu_name)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("timeout", [0, -1, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(tool, timeout):
    with pytest.raises(ValidationError):
        validate_args(tool, vecu_name="MyVEcu", timeout=timeout)


# --- generate_vecu_bsw -------------------------------------------------------------------------


def test_generate_vecu_bsw_success(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.EcuConfiguration.StartBswGeneration.return_value = _bsw_result()
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.generate_vecu_bsw(vecu_name="MyVEcu")

    # assert
    assert result == {
        "status": "success",
        "message": "Execution of BSW generation for V-ECU Implementation was successful.",
    }
    v_ecu.EcuConfiguration.StartBswGeneration.assert_called_once_with("V-ECU Implementation")


def test_generate_vecu_bsw_success_when_error_messages_is_none(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.EcuConfiguration.StartBswGeneration.return_value = SimpleNamespace(ErrorMessages=None)
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.generate_vecu_bsw(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"


def test_generate_vecu_bsw_reports_generation_errors(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.EcuConfiguration.StartBswGeneration.return_value = _bsw_result("First error", "Second error")
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.generate_vecu_bsw(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "BSW_GENERATION_FAILED"
    assert result["message"] == "Execution of BSW generation for V-ECU Implementation failed."
    assert result["details"] == "First error\nSecond error"


def test_generate_vecu_bsw_com_failure(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.EcuConfiguration.StartBswGeneration.side_effect = RuntimeError("COM failure")
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.generate_vecu_bsw(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["details"] == "COM failure"


# --- export_vecu_fmu ---------------------------------------------------------------------------


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_success_defaults_to_project_directory(app):
    # arrange
    v_ecu = _vecu()
    _set_vecus(app, v_ecu)
    expected_fmu = os.path.abspath(os.path.join(os.path.dirname(PROJECT_FILE), "MyVEcu.fmu"))

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result == {
        "status": "success",
        "fmu_file": expected_fmu,
        "message": f"V-ECU FMU exported successfully to '{expected_fmu}'.",
    }
    export_settings = v_ecu.GetNewExportFmuSettings.return_value
    assert export_settings.TargetPath == expected_fmu
    v_ecu.ExportFmu.assert_called_once_with(export_settings)


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_uses_target_directory(app):
    # arrange
    v_ecu = _vecu()
    _set_vecus(app, v_ecu)
    target_directory = os.path.join("D:\\", "Out")

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu", target_directory=target_directory)

    # assert
    assert result["status"] == "success"
    assert result["fmu_file"] == os.path.abspath(os.path.join(target_directory, "MyVEcu.fmu"))


def test_export_vecu_fmu_reuses_existing_build_script(app, monkeypatch):
    # arrange
    v_ecu = _vecu(build_script="C:\\Build\\existing_build.py")
    _set_vecus(app, v_ecu)
    monkeypatch.setattr(vecu_export.os.path, "isfile", lambda path: path == "C:\\Build\\existing_build.py")
    removed = []
    monkeypatch.setattr(vecu_export.os, "remove", removed.append)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    v_ecu.GenerateBuildScript.assert_not_called()
    v_ecu.DefaultBuildScriptFile.Evaluate.assert_not_called()
    assert not removed


def test_export_vecu_fmu_removes_stale_default_build_script_and_regenerates(app, monkeypatch):
    # arrange
    v_ecu = _vecu(build_script="C:\\Build\\missing_build.py", default_build_script="C:\\Build\\default_build.py")
    _set_vecus(app, v_ecu)
    monkeypatch.setattr(vecu_export.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(vecu_export.os.path, "exists", lambda path: path == "C:\\Build\\default_build.py")
    removed = []
    monkeypatch.setattr(vecu_export.os, "remove", removed.append)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    assert removed == ["C:\\Build\\default_build.py"]
    v_ecu.GenerateBuildScript.assert_called_once()


def test_export_vecu_fmu_generates_build_script_when_default_does_not_exist(app, no_files):
    # arrange
    v_ecu = _vecu()
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    assert not no_files
    v_ecu.GenerateBuildScript.assert_called_once()


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_generates_build_script_when_build_script_property_fails(app):
    # arrange
    v_ecu = _vecu()
    type(v_ecu).BuildScript = property(lambda _self: (_ for _ in ()).throw(AttributeError("no BuildScript")))
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    v_ecu.GenerateBuildScript.assert_called_once()


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_ignores_only_expected_errors_and_reports_build_log(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.GetNewExportFmuSettings.return_value = SimpleNamespace(Result=_build_result(
        _build_message("Error(102,110,5)"),
        _build_message("Error(95,1140,108)"),
        _build_message("Error(95,1140,151)"),
        _build_message("Warning(1,2,3)", severity="Warning"),
    ))
    _set_vecus(app, v_ecu)
    build_log = _build_message(
        "Info(102,110,13)", "Writing the build log to: C:\\Build\\build.log", severity="Info",
    )
    app.Messages.Elements = [
        _build_message("Info(95,1140,143)", severity="Info", children=[
            _build_message("Info(1,1,1)", severity="Info", children=[build_log]),
        ]),
    ]

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FMU_EXPORT_FAILED"
    assert "Review the build log" in result["message"]
    assert result["details"] == "Writing the build log to: C:\\Build\\build.log"


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_reports_build_log_not_found(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.GetNewExportFmuSettings.return_value = SimpleNamespace(
        Result=_build_result(_build_message("Error(102,110,5)")),
    )
    _set_vecus(app, v_ecu)
    app.Messages.Elements = [_build_message("Info(95,1140,143)", severity="Info")]

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["error_code"] == "FMU_EXPORT_FAILED"
    assert result["details"].startswith("Build log location not found.")


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_reports_unexpected_errors_with_children(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.GetNewExportFmuSettings.return_value = SimpleNamespace(Result=_build_result(
        _build_message("Error(102,110,5)", "Expected"),
        _build_message("Error(9,9,9)", "Compiler failed", children=[
            _build_message("Error(9,9,10)", "Missing header"),
        ]),
    ))
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FMU_EXPORT_FAILED"
    assert "Fix the reported errors" in result["message"]
    assert result["details"] == "Error(9,9,9): Compiler failed\nError(9,9,10): Missing header"


@pytest.mark.usefixtures("no_files")
def test_export_vecu_fmu_com_failure(app):
    # arrange
    v_ecu = _vecu()
    v_ecu.ExportFmu.side_effect = RuntimeError("COM failure")
    _set_vecus(app, v_ecu)

    # act
    result = vecu_export.export_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["details"] == "COM failure"


@pytest.mark.parametrize("target_directory", VALID_ABSOLUTE_DIRS)
def test_export_vecu_fmu_valid_target_directory_accepted_by_schema(target_directory):
    validate_args(vecu_export.export_vecu_fmu, vecu_name="MyVEcu", target_directory=target_directory)


@pytest.mark.parametrize("target_directory", INVALID_ABSOLUTE_DIRS + [""])
def test_export_vecu_fmu_invalid_target_directory_rejected_by_schema(target_directory):
    with pytest.raises(ValidationError):
        validate_args(vecu_export.export_vecu_fmu, vecu_name="MyVEcu", target_directory=target_directory)
