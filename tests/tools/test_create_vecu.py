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


def _component(short_name, options=None, v_ecu=None, autosar_type="COMPOSITION-SW-COMPONENT-TYPE"):
    component = MagicMock()
    component.ShortName = short_name
    component.AUTOSARPathName = f"/SWA/{short_name}"
    component.AutosarType = autosar_type
    component.GetNewCreateVEcuOptions.return_value = options if options is not None else SimpleNamespace()
    component.CreateVEcu.return_value = v_ecu
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


def _set_elements_by_path(app, elements_by_path):
    app.ActiveProject.GetElementsByARPath.side_effect = lambda path: elements_by_path.get(path, [])


def _set_no_vecus(app):
    app.ActiveProject.VEcus.Elements = []
    app.ActiveProject.VEcus.Item.side_effect = RuntimeError("not found")


def test_no_project_open(app):
    # arrange
    app.ActiveProject = None

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


def test_not_started():
    # arrange
    runtime._application = None

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


def test_get_or_create_vecu_reuses_vecu_by_name(app):
    # arrange
    v_ecu = _vecu("MyVEcu")
    app.ActiveProject.VEcus.Item.return_value = v_ecu

    # act
    result = create_vecu.get_or_create_vecu(vecu_name="MyVEcu")

    # assert
    assert result["status"] == "success"
    assert result["created"] is False
    assert result["vecu_name"] == "MyVEcu"
    assert result["system_path"] == "/Systems/MyVEcu_System"
    assert result["ecu_configuration"] == "MyVEcu_EcuConfiguration"
    assert result["message"] == "Reusing existing V-ECU 'MyVEcu'."
    assert runtime._last_vecu is v_ecu
    app.ActiveProject.VEcus.Item.assert_called_once_with("MyVEcu")
    app.ActiveProject.FindChildElements.assert_not_called()


def test_get_or_create_vecu_reuses_first_vecu_when_requested(app):
    # arrange
    first = _vecu("FirstVEcu")
    second = _vecu("SecondVEcu")
    app.ActiveProject.VEcus.Elements = [first, second]

    # act
    result = create_vecu.get_or_create_vecu(reuse_existing_vecu=True)

    # assert
    assert result["status"] == "success"
    assert result["created"] is False
    assert result["vecu_name"] == "FirstVEcu"
    assert result["message"] == "Reusing existing V-ECU 'FirstVEcu'."
    assert runtime._last_vecu is first
    app.ActiveProject.VEcus.Item.assert_not_called()
    app.ActiveProject.FindChildElements.assert_not_called()


def test_get_or_create_vecu_without_name_creates_new_vecu_by_default(app):
    # arrange
    app.ActiveProject.VEcus.Elements = [_vecu("ExistingVEcu")]
    composition = _component("RootComposition", v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert result["vecu_name"] == "NewVEcu"
    composition.CreateVEcu.assert_called_once()
    app.ActiveProject.VEcus.Item.assert_not_called()


def test_get_or_create_vecu_reuse_requested_but_no_vecu_creates_one(app):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition", v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu(reuse_existing_vecu=True)

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    composition.CreateVEcu.assert_called_once()


def test_get_or_create_vecu_reuse_ignores_creation_options(app):
    # arrange
    app.ActiveProject.VEcus.Item.return_value = _vecu("MyVEcu")

    # act
    result = create_vecu.get_or_create_vecu(
        vecu_name="MyVEcu",
        component_path="/SWA/Missing",
        predefined_ecu_configuration="Default ECU Configuration for V-ECU FMUs (FMI 2.0)",
    )

    # assert
    assert result["status"] == "success"
    assert result["created"] is False
    app.ActiveProject.FindChildElements.assert_not_called()
    app.ActiveProject.GetElementsByARPath.assert_not_called()


def test_get_or_create_vecu_creates_vecu_with_defaults(app):
    # arrange
    _set_no_vecus(app)
    options = SimpleNamespace(Ecu="/Ecus/DefaultEcu")
    v_ecu = _vecu("NewVEcu")
    composition = _component("RootComposition", options=options, v_ecu=v_ecu)
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert result["vecu_name"] == "NewVEcu"
    assert result["system_path"] == "/Systems/NewVEcu_System"
    assert result["ecu_configuration"] == "NewVEcu_EcuConfiguration"
    assert result["message"] == "V-ECU created successfully."
    assert result["component_path"] == "/SWA/RootComposition"
    composition.CreateVEcu.assert_called_once_with(options)
    assert vars(options) == {
        "Ecu": "/Ecus/DefaultEcu",
        "PredefinedEcuConfiguration": "Default ECU Configuration for V-ECU FMUs (FMI 3.0)",
        "AutoConfigureAndGenerate": False,
    }
    assert runtime._last_vecu is v_ecu
    app.ActiveProject.FindChildElements.assert_called_once_with("COMPOSITION-SW-COMPONENT-TYPE")


def test_get_or_create_vecu_creates_named_vecu_when_name_not_found(app):
    # arrange
    _set_no_vecus(app)
    options = SimpleNamespace(Ecu="/Ecus/DefaultEcu")
    composition = _component("RootComposition", options=options, v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu(vecu_name="NewVEcu")

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert options.Ecu == "/Ecus/NewVEcu"
    app.ActiveProject.VEcus.Item.assert_called_once_with("NewVEcu")


def test_get_or_create_vecu_applies_all_given_options(app):
    # arrange
    _set_no_vecus(app)
    options = SimpleNamespace(Ecu="/Ecus/DefaultEcu")
    composition = _component("RootComposition", options=options, v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu(
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


def test_get_or_create_vecu_vecu_name_without_ecu_prefix(app):
    # arrange
    _set_no_vecus(app)
    options = SimpleNamespace(Ecu="DefaultEcu")
    composition = _component("RootComposition", options=options, v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu(vecu_name="NewVEcu")

    # assert
    assert result["status"] == "success"
    assert options.Ecu == "NewVEcu"


def test_get_or_create_vecu_creates_fresh_options_per_call(app):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition", v_ecu=_vecu("NewVEcu"))
    first_options = SimpleNamespace()
    second_options = SimpleNamespace()
    composition.GetNewCreateVEcuOptions.side_effect = [first_options, second_options]
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    create_vecu.get_or_create_vecu(system_name="FirstSystem")
    create_vecu.get_or_create_vecu()

    # assert
    assert composition.CreateVEcu.call_args_list[0].args == (first_options,)
    assert composition.CreateVEcu.call_args_list[1].args == (second_options,)
    assert not hasattr(second_options, "SystemName")


def test_get_or_create_vecu_selects_component_by_path(app):
    # arrange
    _set_no_vecus(app)
    first = _component("FirstComposition")
    second = _component("SecondComposition", v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [first, second]})
    _set_elements_by_path(app, {"/SWA/SecondComposition": [second]})

    # act
    result = create_vecu.get_or_create_vecu(component_path="/SWA/SecondComposition")

    # assert
    assert result["status"] == "success"
    assert result["component_path"] == "/SWA/SecondComposition"
    first.GetNewCreateVEcuOptions.assert_not_called()
    first.CreateVEcu.assert_not_called()
    second.CreateVEcu.assert_called_once()
    app.ActiveProject.FindChildElements.assert_not_called()


def test_get_or_create_vecu_accepts_path_of_any_sw_component_type(app):
    # arrange
    _set_no_vecus(app)
    application = _component(
        "AppComponent", v_ecu=_vecu("NewVEcu"), autosar_type="APPLICATION-SW-COMPONENT-TYPE",
    )
    _set_elements_by_path(app, {"/SWA/AppComponent": [application]})

    # act
    result = create_vecu.get_or_create_vecu(component_path="/SWA/AppComponent")

    # assert
    assert result["status"] == "success"
    application.CreateVEcu.assert_called_once()


@pytest.mark.parametrize(
    "type_name, component_name",
    [
        ("APPLICATION-SW-COMPONENT-TYPE", "AppComponent"),
        ("SENSOR-ACTUATOR-SW-COMPONENT-TYPE", "SensorActuator"),
    ],
)
def test_get_or_create_vecu_falls_back_to_other_component_types(app, type_name, component_name):
    # arrange
    _set_no_vecus(app)
    component = _component(component_name, v_ecu=_vecu("NewVEcu"))
    _set_components(app, {type_name: [component]})

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "success"
    component.CreateVEcu.assert_called_once()
    searched = [call.args[0] for call in app.ActiveProject.FindChildElements.call_args_list]
    assert searched[0] == "COMPOSITION-SW-COMPONENT-TYPE"
    assert searched[-1] == type_name


def test_get_or_create_vecu_new_name_creates_additional_vecu(app):
    # arrange
    app.ActiveProject.VEcus.Elements = [_vecu("ExistingVEcu")]
    app.ActiveProject.VEcus.Item.side_effect = RuntimeError("not found")
    composition = _component("RootComposition", v_ecu=_vecu("NewVEcu"))
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu(vecu_name="NewVEcu")

    # assert
    assert result["status"] == "success"
    assert result["created"] is True
    assert result["vecu_name"] == "NewVEcu"
    composition.CreateVEcu.assert_called_once()


@pytest.mark.parametrize(
    "arguments",
    [{"vecu_name": "NewVEcu"}, {"reuse_existing_vecu": True}],
)
def test_get_or_create_vecu_repeated_call_reuses_created_vecu(app, arguments):
    # arrange
    created = _vecu("NewVEcu")
    vecus = {}
    app.ActiveProject.VEcus.Elements = []

    def _item(name):
        if name not in vecus:
            raise RuntimeError("not found")
        return vecus[name]

    def _create(_options):
        vecus[created.Name] = created
        app.ActiveProject.VEcus.Elements = [created]
        return created

    app.ActiveProject.VEcus.Item.side_effect = _item
    composition = _component("RootComposition")
    composition.CreateVEcu.side_effect = _create
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    first = create_vecu.get_or_create_vecu(**arguments)
    second = create_vecu.get_or_create_vecu(**arguments)

    # assert
    assert first["created"] is True
    assert second["status"] == "success"
    assert second["created"] is False
    assert second["vecu_name"] == "NewVEcu"
    composition.CreateVEcu.assert_called_once()


def test_get_or_create_vecu_repeated_call_without_name_creates_new_vecu_each_time(app):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition")
    composition.CreateVEcu.side_effect = [_vecu("VEcu"), _vecu("VEcu_1")]
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    first = create_vecu.get_or_create_vecu()
    second = create_vecu.get_or_create_vecu()

    # assert
    assert first["created"] is True
    assert second["created"] is True
    assert second["vecu_name"] == "VEcu_1"
    assert composition.CreateVEcu.call_count == 2


@pytest.mark.parametrize("removed_argument", [
    {"force_create": True},
    {"component_name": "MyComposition"},
    {"component_type": "APPLICATION-SW-COMPONENT-TYPE"},
])
def test_get_or_create_vecu_removed_arguments_are_rejected_by_schema(removed_argument):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu, **removed_argument)


@pytest.mark.parametrize("component_path", ["MyComposition", "/MyComposition", "/SWA/", "/SWA/1Invalid"])
def test_get_or_create_vecu_invalid_component_path_rejected_by_schema(component_path):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu, component_path=component_path)


@pytest.mark.parametrize("argument", ["vecu_name", "system_name"])
@pytest.mark.parametrize("name", ["", "1Invalid", "My VEcu", "My-VEcu", "/MyVEcu", "A" * 129])
def test_get_or_create_vecu_invalid_short_name_rejected_by_schema(argument, name):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu, **{argument: name})


@pytest.mark.parametrize("argument", ["vecu_name", "system_name"])
@pytest.mark.parametrize("name", ["A", "MyVEcu", "My_VEcu_2", "A" * 128])
def test_get_or_create_vecu_valid_short_name_accepted_by_schema(argument, name):
    validate_args(create_vecu.get_or_create_vecu, **{argument: name})


def test_get_or_create_vecu_component_path_not_found(app):
    # arrange
    _set_no_vecus(app)
    _set_elements_by_path(app, {})

    # act
    result = create_vecu.get_or_create_vecu(component_path="/SWA/Missing")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "AUTOSAR_ELEMENT_NOT_FOUND"
    assert "'/SWA/Missing'" in result["message"]
    assert runtime._last_vecu is None


def test_get_or_create_vecu_component_path_ambiguous(app):
    # arrange
    _set_no_vecus(app)
    _set_elements_by_path(app, {"/SWA/Twin": [_component("Twin"), _component("Twin")]})

    # act
    result = create_vecu.get_or_create_vecu(component_path="/SWA/Twin")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "AMBIGUOUS_AUTOSAR_PATH"


def test_get_or_create_vecu_component_path_is_not_a_sw_component_type(app):
    # arrange
    _set_no_vecus(app)
    package = _component("SWA", autosar_type="AR-PACKAGE")
    _set_elements_by_path(app, {"/SWA/Pkg": [package]})

    # act
    result = create_vecu.get_or_create_vecu(component_path="/SWA/Pkg")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_AUTOSAR_ELEMENT_TYPE"
    package.CreateVEcu.assert_not_called()


def test_get_or_create_vecu_no_suitable_component(app):
    # arrange
    _set_no_vecus(app)
    _set_components(app, {})

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "COMPONENT_NOT_FOUND"
    assert "no suitable sw component" in result["message"].lower()


def test_get_or_create_vecu_creation_returns_none_with_messages(app):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition", v_ecu=None)
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})
    app.Messages.Elements = [
        _message_with_errors(("Error(1,2,3)", "Older error")),
        _message_without_errors(),
        _message_with_errors(("Error(4,5,6)", "First error"), ("Error(7,8,9)", "Second error")),
        _message_without_errors(),
    ]

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_CREATION_FAILED"
    assert json.loads(result["details"]) == [
        {"MessageIdentifier": "Error(4,5,6)", "MessageText": "First error"},
        {"MessageIdentifier": "Error(7,8,9)", "MessageText": "Second error"},
    ]
    assert runtime._last_vecu is None


def test_get_or_create_vecu_creation_returns_none_without_messages(app):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition", v_ecu=None)
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})
    app.Messages.Elements = [_message_without_errors()]

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_CREATION_FAILED"
    assert "details" not in result


@pytest.mark.parametrize("failing_call", ["GetNewCreateVEcuOptions", "CreateVEcu"])
def test_get_or_create_vecu_com_failure(app, failing_call):
    # arrange
    _set_no_vecus(app)
    composition = _component("RootComposition")
    getattr(composition, failing_call).side_effect = RuntimeError("COM failure")
    _set_components(app, {"COMPOSITION-SW-COMPONENT-TYPE": [composition]})

    # act
    result = create_vecu.get_or_create_vecu()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert result["details"] == "COM failure"
    assert runtime._last_vecu is None


@pytest.mark.parametrize("timeout", [0, -1, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(timeout):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu, timeout=timeout)


@pytest.mark.parametrize(
    "arguments",
    [
        {"predefined_ecu_configuration": "Default ECU Configuration for V-ECU FMUs (FMI 4.0)"},
        {"rte_calibration_support": "ONLINE"},
        {"rte_simulation_logging": "ALL"},
    ],
)
def test_get_or_create_vecu_invalid_literal_rejected_by_schema(arguments):
    with pytest.raises(ValidationError):
        validate_args(create_vecu.get_or_create_vecu, **arguments)
