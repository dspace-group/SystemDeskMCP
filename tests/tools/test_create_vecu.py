"""Tests for create_vecu behavior."""
# pylint: disable=protected-access

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import create_vecu
from systemdesk_mcp import common, runtime
from tests.helpers import validate_args


def _component(short_name):
    component = MagicMock()
    component.ShortName = short_name
    return component


def _vecu(name="MyVEcu"):
    v_ecu = MagicMock()
    v_ecu.Name = name
    v_ecu.System.AUTOSARPathName = f"/Systems/{name}_System"
    v_ecu.EcuConfiguration = f"{name}_EcuConfiguration"
    return v_ecu


def _message_with_errors(*errors):
    message = MagicMock()
    message.ErrorMessages.Count = len(errors)
    message.ErrorMessages.Elements = [
        SimpleNamespace(MessageIdentifier=identifier, MessageText=text)
        for identifier, text in errors
    ]
    return message


def _message_without_errors():
    message = MagicMock()
    message.ErrorMessages.Count = 0
    return message


def _set_components(app, components_by_type):
    app.ActiveProject.FindChildElements.side_effect = lambda type_name: components_by_type.get(type_name, [])


def test_create_vecu_options_success_composition(app):
    # arrange
    composition = _component("RootComposition")
    options = SimpleNamespace(Ecu="/Ecus/DefaultEcu")
    composition.GetNewCreateVEcuOptions.return_value = options
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.create_vecu_options()

    # assert
    assert result["status"] == "success"
    assert result["component_name"] == "RootComposition"
    assert "get_or_create_vecu_fmu" in result["message"]
    assert runtime._vecu_options is options
    assert options.PredefinedEcuConfiguration == "Default ECU Configuration for V-ECU FMUs (FMI 3.0)"
    assert options.AutoConfigureAndGenerate is False
    assert options.Ecu == "/Ecus/DefaultEcu"
    app.ActiveProject.FindChildElements.assert_called_once_with("COMPOSITION-SW-COMPONENT-TYPE")


def test_create_vecu_options_applies_all_given_options(app):
    # arrange
    composition = _component("RootComposition")
    options = SimpleNamespace(Ecu="/Ecus/DefaultEcu")
    composition.GetNewCreateVEcuOptions.return_value = options
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.create_vecu_options(
        vecu_name="NewVEcu",
        system_name="NewSystem",
        predefined_ecu_configuration="Default ECU Configuration for V-ECU FMUs with CAN Driver (FMI 2.0)",
        generate_dap_for_atomic_ports=True,
        generate_dap_for_root_composition_ports=False,
        create_runnable_mapping=True,
        rte_calibration_support="OFFLINE",
        rte_simulation_logging="SIMULATION",
        rte_measurement_support=True,
        auto_configure_and_generate=True,
    )

    # assert
    assert result["status"] == "success"
    assert vars(options) == {
        "Ecu": "/Ecus/NewVEcu",
        "SystemName": "NewSystem",
        "PredefinedEcuConfiguration": "Default ECU Configuration for V-ECU FMUs with CAN Driver (FMI 2.0)",
        "GenerateDapForAtomicPorts": True,
        "GenerateDapForRootCompositionPorts": False,
        "CreateRunnableMapping": True,
        "RteCalibrationSupport": "OFFLINE",
        "RteSimulationLogging": "SIMULATION",
        "RteMeasurementSupport": True,
        "AutoConfigureAndGenerate": True,
    }


def test_create_vecu_options_vecu_name_without_ecu_prefix(app):
    # arrange
    composition = _component("RootComposition")
    options = SimpleNamespace(Ecu="DefaultEcu")
    composition.GetNewCreateVEcuOptions.return_value = options
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.create_vecu_options(vecu_name="NewVEcu")

    # assert
    assert result["status"] == "success"
    assert options.Ecu == "NewVEcu"


def test_create_vecu_options_selects_component_by_name(app):
    # arrange
    first = _component("FirstComposition")
    second = _component("SecondComposition")
    options = SimpleNamespace()
    second.GetNewCreateVEcuOptions.return_value = options
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [first, second]})

    # act
    result = create_vecu.create_vecu_options(component_name="SecondComposition")

    # assert
    assert result["status"] == "success"
    assert result["component_name"] == "SecondComposition"
    first.GetNewCreateVEcuOptions.assert_not_called()
    assert runtime._vecu_options is options


@pytest.mark.parametrize(
    "type_name, component_name",
    [
        ("APPLICATION-SW-COMPONENT-TYPE", "AppComponent"),
        ("SENSOR-ACTUATOR-SW-COMPONENT-TYPE", "SensorActuator"),
    ],
)
def test_create_vecu_options_falls_back_to_other_component_types(app, type_name, component_name):
    # arrange
    component = _component(component_name)
    component.GetNewCreateVEcuOptions.return_value = SimpleNamespace()
    _set_components(app, {type_name: [component]})

    # act
    result = create_vecu.create_vecu_options()

    # assert
    assert result["status"] == "success"
    assert result["component_name"] == component_name
    searched = [call.args[0] for call in app.ActiveProject.FindChildElements.call_args_list]
    assert searched[0] == "COMPOSITION-SW-COMPONENT-TYPE"
    assert searched[-1] == type_name


def test_create_vecu_options_explicit_component_type_is_searched_alone(app):
    # arrange
    application = _component("AppComponent")
    application.GetNewCreateVEcuOptions.return_value = SimpleNamespace()
    _set_components(app, {
        "COMPOSITION-SW-COMPONENT-TYPE": [_component("RootComposition")],
        "APPLICATION-SW-COMPONENT-TYPE": [application],
    })

    # act
    result = create_vecu.create_vecu_options(component_type="APPLICATION-SW-COMPONENT-TYPE")

    # assert
    assert result["status"] == "success"
    assert result["component_name"] == "AppComponent"
    app.ActiveProject.FindChildElements.assert_called_once_with("APPLICATION-SW-COMPONENT-TYPE")


def test_create_vecu_options_component_name_not_found(app):
    # arrange
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [_component("RootComposition")]})

    # act
    result = create_vecu.create_vecu_options(component_name="Missing")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "COMPONENT_NOT_FOUND"
    assert "'Missing'" in result["message"]
    assert runtime._vecu_options is None


def test_create_vecu_options_ecu_flat_view_is_not_selectable_by_name(app):
    # arrange
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [_component("EcuFlatView")]})

    # act
    result = create_vecu.create_vecu_options(component_name="EcuFlatView")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "COMPONENT_NOT_FOUND"


def test_create_vecu_options_no_suitable_component(app):
    # arrange
    _set_components(app, {})

    # act
    result = create_vecu.create_vecu_options()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "COMPONENT_NOT_FOUND"
    assert "no suitable sw component" in result["message"].lower()
    assert runtime._vecu_options is None


@pytest.mark.parametrize("tool", [create_vecu.create_vecu_options, create_vecu.get_or_create_vecu_fmu])
def test_no_project_open(app, tool):
    # arrange
    app.ActiveProject = None

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


@pytest.mark.parametrize("tool", [create_vecu.create_vecu_options, create_vecu.get_or_create_vecu_fmu])
def test_not_started(tool):
    # arrange
    runtime._application = None

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


def test_create_vecu_options_com_failure(app):
    # arrange
    composition = _component("RootComposition")
    composition.GetNewCreateVEcuOptions.side_effect = RuntimeError("COM failure")
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.create_vecu_options()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "VECU_OPTIONS_CREATION_FAILED"
    assert result["details"] == "COM failure"
    assert runtime._vecu_options is None


def test_get_or_create_vecu_fmu_reuses_vecu_by_name(app):
    # arrange
    v_ecu = _vecu("MyVEcu")
    app.ActiveProject.VEcus.Item.return_value = v_ecu

    # act
    result = create_vecu.get_or_create_vecu_fmu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    assert result["created"] is False
    assert result["vecu_name"] == "MyVEcu"
    assert result["system_path"] == "/Systems/MyVEcu_System"
    assert result["ecu_configuration"] == "MyVEcu_EcuConfiguration"
    assert result["message"] == "Reusing existing V-ECU 'MyVEcu'."
    assert runtime._last_vecu is v_ecu
    app.ActiveProject.VEcus.Item.assert_called_once_with("MyVEcu")


def test_get_or_create_vecu_fmu_vecu_name_not_found(app):
    # arrange
    app.ActiveProject.VEcus.Item.side_effect = RuntimeError("not found")

    # act
    result = create_vecu.get_or_create_vecu_fmu(vecu_name="Missing")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "'Missing'" in result["message"]
    assert runtime._last_vecu is None


def test_get_or_create_vecu_fmu_reuses_first_vecu(app):
    # arrange
    first = _vecu("FirstVEcu")
    second = _vecu("SecondVEcu")
    app.ActiveProject.VEcus.Elements = [first, second]

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "success"
    assert result["created"] is False
    assert result["vecu_name"] == "FirstVEcu"
    assert result["message"] == "Reusing existing V-ECU 'FirstVEcu'."
    assert runtime._last_vecu is first
    app.ActiveProject.VEcus.Item.assert_not_called()


def test_get_or_create_vecu_fmu_creates_vecu(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    options = SimpleNamespace()
    runtime._vecu_options = options
    composition = _component("RootComposition")
    v_ecu = _vecu("NewVEcu")
    composition.CreateVEcu.return_value = v_ecu
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert result["vecu_name"] == "NewVEcu"
    assert result["system_path"] == "/Systems/NewVEcu_System"
    assert result["ecu_configuration"] == "NewVEcu_EcuConfiguration"
    assert result["message"] == "V-ECU created successfully."
    composition.CreateVEcu.assert_called_once_with(options)
    assert runtime._last_vecu is v_ecu
    assert runtime._vecu_options is options


def test_get_or_create_vecu_fmu_force_create_ignores_existing_vecus(app):
    # arrange
    app.ActiveProject.VEcus.Elements = [_vecu("ExistingVEcu")]
    runtime._vecu_options = SimpleNamespace()
    composition = _component("RootComposition")
    composition.CreateVEcu.return_value = _vecu("NewVEcu")
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu_fmu(vecu_name="ExistingVEcu", force_create=True)

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert result["vecu_name"] == "NewVEcu"
    app.ActiveProject.VEcus.Item.assert_not_called()


def test_get_or_create_vecu_fmu_options_missing(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    runtime._vecu_options = None

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_OPTIONS_MISSING"
    assert "create_vecu_options" in result["message"]


def test_get_or_create_vecu_fmu_component_name_not_found(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    options = SimpleNamespace()
    runtime._vecu_options = options
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [_component("RootComposition")]})

    # act
    result = create_vecu.get_or_create_vecu_fmu(component_name="Missing")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "COMPONENT_NOT_FOUND"
    assert "'Missing'" in result["message"]
    assert runtime._vecu_options is options


def test_get_or_create_vecu_fmu_creation_returns_none_with_messages(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    options = SimpleNamespace()
    runtime._vecu_options = options
    composition = _component("RootComposition")
    composition.CreateVEcu.return_value = None
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})
    app.Messages.Elements = [
        _message_with_errors(("Error(1,2,3)", "Older error")),
        _message_without_errors(),
        _message_with_errors(("Error(4,5,6)", "First error"), ("Error(7,8,9)", "Second error")),
        _message_without_errors(),
    ]

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "VECU_CREATION_FAILED"
    assert json.loads(result["details"]) == [
        {"MessageIdentifier": "Error(4,5,6)", "MessageText": "First error"},
        {"MessageIdentifier": "Error(7,8,9)", "MessageText": "Second error"},
    ]
    assert runtime._last_vecu is None
    assert runtime._vecu_options is options


def test_get_or_create_vecu_fmu_creation_returns_none_without_messages(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    runtime._vecu_options = SimpleNamespace()
    composition = _component("RootComposition")
    composition.CreateVEcu.return_value = None
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})
    app.Messages.Elements = [_message_without_errors()]

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "VECU_CREATION_FAILED"
    assert "details" not in result


def test_get_or_create_vecu_fmu_com_failure(app):
    # arrange
    app.ActiveProject.VEcus.Elements = []
    runtime._vecu_options = SimpleNamespace()
    composition = _component("RootComposition")
    composition.CreateVEcu.side_effect = RuntimeError("COM failure")
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu_fmu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "VECU_CREATION_FAILED"
    assert result["details"] == "COM failure"
    assert runtime._last_vecu is None


@pytest.mark.parametrize("timeout", [0, -1, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(timeout):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.create_vecu_options, timeout=timeout)
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu_fmu, timeout=timeout)


@pytest.mark.parametrize(
    "arguments",
    [
        {"predefined_ecu_configuration": "Default ECU Configuration for V-ECU FMUs (FMI 4.0)"},
        {"rte_calibration_support": "ONLINE"},
        {"rte_simulation_logging": "ALL"},
    ],
)
def test_create_vecu_options_invalid_literal_rejected_by_schema(arguments):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.create_vecu_options, **arguments)
