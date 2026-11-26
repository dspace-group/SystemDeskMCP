"""Tests for the V-ECU configuration tools (no SystemDesk installation required)."""
# pylint: disable=protected-access,too-few-public-methods,invalid-name
# The fakes mirror the SystemDesk COM object model and need no docstrings.
# pylint: disable=missing-function-docstring,missing-class-docstring,too-many-instance-attributes
# pylint: disable=too-many-arguments,too-many-positional-arguments

import typing
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from systemdesk_mcp import common, runtime
from systemdesk_mcp.tools import vecu_configuration as vc
from systemdesk_mcp.tools.vecu_configuration import InterventionPointSpec
from tests.helpers import validate_args

READ, WRITE = 0, 2
KIND_OPTION_READ_AND_WRITE_ONLY = 3
NO_SERVICE, DAP_SERVICE, RTE_SERVICE_PORT = 0, 1, 2


# --- Fakes of the SystemDesk object model ---

class Collection:
    """Named collection with Elements, Item(name) -> None if missing, and Add(name)."""

    def __init__(self, factory=None, elements=()):
        self.Elements = list(elements)
        self._factory = factory

    def Item(self, name):
        return next((e for e in self.Elements if e.ShortName == name), None)

    def Add(self, name):
        element = self._factory(name)
        self.Elements.append(element)
        return element

    @property
    def Count(self):
        return len(self.Elements)


class Task:
    def __init__(self, name, priority=1, period=None):
        self.ShortName = name
        self.OsTaskPriority = priority
        self.OsTaskActivation = None
        self._period = period

    @property
    def IsOsTaskPeriodDefined(self):
        return self._period is not None

    @property
    def OsTaskPeriod(self):
        if self._period is None:
            raise RuntimeError("period not defined")
        return self._period

    @OsTaskPeriod.setter
    def OsTaskPeriod(self, value):
        self._period = value


def _mapping(name):
    return SimpleNamespace(ShortName=name, RteEventRef=None, RteMappedToTaskRef=None, RtePositionInTask=None)


def _instance(name):
    return SimpleNamespace(ShortName=name, RteEventToTaskMappings=Collection(_mapping))


def _event(name, event_type, runnable, period=None):
    return SimpleNamespace(ShortName=name, AutosarType=event_type, Period=period,
                           StartOnEventRef=SimpleNamespace(ShortName=runnable))


def _component(name, events, autosar_type="APPLICATION-SW-COMPONENT-TYPE"):
    behavior = SimpleNamespace(Events=SimpleNamespace(Elements=list(events)))
    return SimpleNamespace(ShortName=name, TypeTref=SimpleNamespace(
        AutosarType=autosar_type, InternalBehaviors=SimpleNamespace(Elements=[behavior])))


class Point:
    """Possible or existing RTE intervention point."""

    def __init__(self, component, port, data_element, kind, runnable="Run", service=None):
        self.ContextSwComponentPrototype = component
        self.Kind = kind
        self.ShortName = f"Rte_{'IRead' if kind == READ else 'IWrite'}_{component.ShortName}_{runnable}_{port}_{data_element}"
        self.ElementRef = SimpleNamespace(AccessedVariable=SimpleNamespace(AutosarVariableIref=SimpleNamespace(
            PortPrototypeRef=SimpleNamespace(ShortName=port),
            TargetDataPrototypeRef=SimpleNamespace(ShortName=data_element))))
        self.service = service

    def GetInterventionService(self):
        return self.service


class IrvPoint:
    """Inter-runnable variable point: has no port."""

    def __init__(self, component):
        self.ContextSwComponentPrototype = component
        self.Kind = READ
        self.ShortName = "Rte_IrvIRead_X"
        self.ElementRef = SimpleNamespace(AccessedVariable=SimpleNamespace())


class Service:
    def __init__(self, name):
        self.ShortName = name
        self.InputType = NO_SERVICE
        self.OutputType = NO_SERVICE
        self.points = []

    def AddInterventionPoints(self, points):
        for point in points:
            point.service = self
            self.points.append(point)


class RteInterventions:
    def __init__(self, possible=(), existing=()):
        self.possible = list(possible)
        self.existing = list(existing)
        self.InterventionServices = SimpleNamespace(Elements=[], AddNew=self._add_service)
        self.InterventionEnableCodeGeneration = False
        self.kind_options = []
        self.delayed_mode_calls = []

    def EnterDelayedMode(self):
        self.delayed_mode_calls.append("enter")

    def ExitDelayedMode(self):
        self.delayed_mode_calls.append("exit")

    def _add_service(self, name):
        service = Service(name)
        self.InterventionServices.Elements.append(service)
        return service

    def GetPossibleInterventionPoints(self, kind_option):
        self.kind_options.append(kind_option)
        return list(self.possible)

    def GetInterventionPoints(self):
        return list(self.existing)

    def CreateInterventionPoints(self, points):
        for point in points:
            self.possible.remove(point)
            self.existing.append(point)
        return list(points)


class World:
    """A project with V-ECUs, one ECU configuration with Os, Rte and RTE interventions."""

    def __init__(self, app, vecu_names=("Test_Ecu",)):
        self.tasks = Collection(Task)
        self.os_cfg = SimpleNamespace(OsTasks=self.tasks)
        self.rte_cfg = SimpleNamespace(RteSwComponentInstances=Collection(_instance))
        self.events = [
            _event("InitEvent", "INIT-EVENT", "Init"),
            _event("Calc10ms", "TIMING-EVENT", "Calc", 0.01),
            _event("Corr10ms", "TIMING-EVENT", "Corr", 0.01),
            _event("ModeEvent", "SWC-MODE-SWITCH-EVENT", "OnMode"),
        ]
        self.component = _component("Ctrl", self.events)
        self.service_component = _component("Rte", [], "SERVICE-SW-COMPONENT-TYPE")
        self.rti = RteInterventions()
        self.schedule_calls = []
        self.ecu = MagicMock()
        self.ecu.Name = "EcuConfiguration_Test_Ecu"
        self.ecu.GetModuleConfigurationByDefinitionName.side_effect = self._module
        self.ecu.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements = [
            self.component, self.service_component]
        self.ecu.RteInterventions = self.rti
        self.ecu.ClearRunnableSchedule.side_effect = self._clear_runnable_schedule
        self.ecu.GenerateRunnableSchedule.side_effect = self._generate_runnable_schedule
        self.modules = {"Os": self.os_cfg, "Rte": self.rte_cfg}
        self.vecus = [SimpleNamespace(Name=name, EcuConfiguration=self.ecu) for name in vecu_names]
        app.ActiveProject.VEcus.Elements = self.vecus
        app.ActiveProject.VEcus.Item.side_effect = self._vecu

    def _module(self, name, _inactive):
        return self.modules.get(name)

    def _vecu(self, name):
        found = next((v for v in self.vecus if v.Name == name), None)
        if found is None:
            raise RuntimeError("not found")
        return found

    def _clear_runnable_schedule(self):
        """Like SystemDesk, delete all tasks with their mappings; also succeeds if there is nothing to delete."""
        self.schedule_calls.append("clear")
        self.tasks.Elements.clear()
        for instance in self.rte_cfg.RteSwComponentInstances.Elements:
            instance.RteEventToTaskMappings.Elements.clear()
        return True

    def _generate_runnable_schedule(self):
        """Like SystemDesk, create the tasks if required and map only the events that are not mapped yet."""
        self.schedule_calls.append("generate")
        task10 = self.tasks.Item("Task10ms") or self.tasks.Add("Task10ms")
        task10.OsTaskPeriod = 0.01
        task_init = self.tasks.Item("TaskInit") or self.tasks.Add("TaskInit")
        instance = self.rte_cfg.RteSwComponentInstances.Item("Ctrl")
        for event_name, task, position in [("InitEvent", task_init, 0), ("Calc10ms", task10, 1), ("Corr10ms", task10, 0)]:
            if instance is None or instance.RteEventToTaskMappings.Item(event_name) is None:
                self.map(event_name, task, position)
                instance = self.rte_cfg.RteSwComponentInstances.Item("Ctrl")
        return True

    def map(self, event_name, task, position):
        instance = self.rte_cfg.RteSwComponentInstances.Item("Ctrl") or self.rte_cfg.RteSwComponentInstances.Add("Ctrl")
        mapping = instance.RteEventToTaskMappings.Add(event_name)
        mapping.RteEventRef = next(e for e in self.events if e.ShortName == event_name)
        mapping.RteMappedToTaskRef = task
        mapping.RtePositionInTask = position
        return mapping


@pytest.fixture(name="world")
def _world(app):
    return World(app)


# --- Common preconditions ---

TOOLS = [vc.get_vecu_configuration, vc.generate_runnable_mapping, vc.configure_rte_interventions]


@pytest.mark.parametrize("tool", TOOLS)
def test_not_started(tool):
    # arrange
    runtime._application = None

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


@pytest.mark.parametrize("tool", TOOLS)
def test_no_project_open(app, tool):
    # arrange
    app.ActiveProject = None

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"


@pytest.mark.parametrize("tool", TOOLS)
def test_no_vecu_in_project(app, tool):
    # arrange
    World(app, vecu_names=())

    # act
    result = tool()

    # assert
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "does not contain a V-ECU" in result["message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_unknown_vecu_name_lists_available_vecus(app, tool):
    # arrange
    World(app, vecu_names=("Controller", "Test_Ecu"))

    # act
    result = tool(vecu_name="Missing")

    # assert
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "Controller, Test_Ecu" in result["message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_single_vecu_is_used_by_default(app, tool):
    # arrange
    World(app, vecu_names=("Controller",))

    # act
    result = tool()

    # assert
    assert result["status"] == "success"
    assert result["vecu_name"] == "Controller"


@pytest.mark.parametrize("tool", TOOLS)
def test_several_vecus_without_name_require_selection(app, tool):
    # arrange
    World(app, vecu_names=("Controller", "Test_Ecu"))

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "VECU_SELECTION_REQUIRED"
    assert "Controller, Test_Ecu" in result["message"]
    assert "Ask the user" in result["message"]


def test_named_vecu_is_used_among_several(app):
    # arrange
    World(app, vecu_names=("Controller", "Test_Ecu"))

    # act
    result = vc.get_vecu_configuration(vecu_name="Test_Ecu", include=["os_tasks"])

    # assert
    assert result["status"] == "success"
    assert result["vecu_name"] == "Test_Ecu"


@pytest.mark.parametrize("tool", TOOLS)
def test_com_failure_is_permanent_unexpected_error(world, tool):
    # arrange
    world.ecu.GetModuleConfigurationByDefinitionName.side_effect = RuntimeError("COM failure")
    world.ecu.EcuExtractSystem = property(lambda _: None)
    world.ecu.RteInterventions = SimpleNamespace()

    # act
    result = tool()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"


# --- get_vecu_configuration ---

def test_get_returns_all_lists_by_default(world):
    # arrange
    task10 = world.tasks.Add("Task10ms")
    task10.OsTaskPeriod = 0.01
    task10.OsTaskPriority = 10
    world.tasks.Add("TaskInit").OsTaskPriority = 20
    world.map("Calc10ms", task10, 1)

    # act
    result = vc.get_vecu_configuration()

    # assert
    assert result["status"] == "success"
    assert result["vecu_name"] == "Test_Ecu"
    assert list(result) == [
        "status", "vecu_name", "runnables", "os_tasks",
        "possible_rte_intervention_points", "existing_rte_intervention_points",
    ]
    assert result["runnables"][1] == {
        "component": "Ctrl", "runnable": "Calc", "rte_event": "Calc10ms", "event_type": "TIMING-EVENT",
        "period_ms": 10.0, "mapped_task": "Task10ms", "position": 1,
    }
    assert result["runnables"][0] == {
        "component": "Ctrl", "runnable": "Init", "rte_event": "InitEvent", "event_type": "INIT-EVENT",
        "period_ms": None, "mapped_task": None, "position": None,
    }
    assert result["os_tasks"] == [
        {"name": "Task10ms", "period_ms": 10.0, "priority": 10},
        {"name": "TaskInit", "period_ms": None, "priority": 20},
    ]


def test_get_returns_only_included_lists(world):
    # act
    result = vc.get_vecu_configuration(include=["os_tasks", "runnables"])

    # assert
    assert set(result) == {"status", "vecu_name", "runnables", "os_tasks"}
    assert not world.schedule_calls


def test_get_runnables_without_rte_module(world):
    # arrange
    del world.modules["Rte"]

    # act
    result = vc.get_vecu_configuration(include=["runnables"])

    # assert
    assert [r["mapped_task"] for r in result["runnables"]] == [None, None, None, None]


@pytest.mark.parametrize("autosar_type", ["COMPOSITION-SW-COMPONENT-TYPE", "PARAMETER-SW-COMPONENT-TYPE"])
def test_get_runnables_skips_component_without_internal_behaviors(world, autosar_type):
    # arrange
    component = SimpleNamespace(ShortName="NoBehavior", TypeTref=SimpleNamespace(AutosarType=autosar_type))
    world.ecu.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements.append(component)

    # act
    result = vc.get_vecu_configuration(include=["runnables"])

    # assert
    assert result["status"] == "success"
    assert {r["component"] for r in result["runnables"]} == {"Ctrl"}


def test_get_os_tasks_without_os_module(world):
    # arrange
    del world.modules["Os"]

    # act
    result = vc.get_vecu_configuration(include=["os_tasks"])

    # assert
    assert result["os_tasks"] == []


def test_get_possible_intervention_points_are_unique_and_skip_irv_and_service_components(world):
    # arrange
    world.rti.possible = [
        Point(world.component, "RpIn", "Value", READ, runnable="A"),
        Point(world.component, "RpIn", "Value", READ, runnable="B"),
        Point(world.component, "PpOut", "Result", WRITE),
        Point(world.service_component, "Pp_Service_output", "Result", WRITE),
        IrvPoint(world.component),
    ]

    # act
    result = vc.get_vecu_configuration(include=["possible_rte_intervention_points"])

    # assert
    assert result["possible_rte_intervention_points"] == [
        {"component": "Ctrl", "port": "RpIn", "data_element": "Value"},
        {"component": "Ctrl", "port": "PpOut", "data_element": "Result"},
    ]
    assert world.rti.kind_options == [KIND_OPTION_READ_AND_WRITE_ONLY]


def test_get_existing_intervention_points_report_service_and_access(world):
    # arrange
    dap = Service("InService")
    dap.InputType = DAP_SERVICE
    port = Service("OutService")
    port.OutputType = RTE_SERVICE_PORT
    world.rti.existing = [
        Point(world.component, "RpIn", "Value", READ, service=dap),
        Point(world.component, "PpOut", "Result", WRITE, service=port),
        Point(world.component, "PpOut", "Other", WRITE),
    ]

    # act
    result = vc.get_vecu_configuration(include=["existing_rte_intervention_points"])

    # assert
    assert [(e["service"], e["access"]) for e in result["existing_rte_intervention_points"]] == [
        ("InService", "dap"), ("OutService", "rte_service_port"), (None, None)]


@pytest.mark.parametrize("include", [[], ["unknown"], ["runnables", "runnables"]])
def test_get_invalid_include_rejected_by_schema(include):
    with pytest.raises(ValidationError):
        validate_args(vc.get_vecu_configuration, include=include)


def test_get_removed_sections_argument_rejected_by_schema():
    with pytest.raises(ValidationError):
        validate_args(vc.get_vecu_configuration, sections=["os_tasks"])


# --- generate_runnable_mapping ---

def test_mapping_clears_existing_tasks_and_mappings_then_generates_mapping(world):
    # arrange
    existing = world.tasks.Add("Existing")
    world.map("Calc10ms", existing, 5)

    # act
    result = vc.generate_runnable_mapping()

    # assert
    assert world.schedule_calls == ["clear", "generate"]
    assert world.tasks.Item("Existing") is None
    assert result == {
        "status": "success",
        "vecu_name": "Test_Ecu",
        "mapping": [
            {"component": "Ctrl", "runnable": "Init", "rte_event": "InitEvent", "event_type": "INIT-EVENT",
             "period_ms": None, "mapped_task": "TaskInit", "position": 0},
            {"component": "Ctrl", "runnable": "Calc", "rte_event": "Calc10ms", "event_type": "TIMING-EVENT",
             "period_ms": 10.0, "mapped_task": "Task10ms", "position": 1},
            {"component": "Ctrl", "runnable": "Corr", "rte_event": "Corr10ms", "event_type": "TIMING-EVENT",
             "period_ms": 10.0, "mapped_task": "Task10ms", "position": 0},
        ],
    }


def test_mapping_is_idempotent(world):
    # act
    first = vc.generate_runnable_mapping()
    second = vc.generate_runnable_mapping()

    # assert
    assert first == second
    assert len(world.tasks.Elements) == 2
    assert len(world.rte_cfg.RteSwComponentInstances.Item("Ctrl").RteEventToTaskMappings.Elements) == 3


def test_mapping_fails_without_generating_if_clear_fails(world):
    # arrange
    world.ecu.ClearRunnableSchedule.side_effect = lambda: False

    # act
    result = vc.generate_runnable_mapping()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_OPERATION_FAILED"
    assert "could not clear" in result["message"]
    world.ecu.GenerateRunnableSchedule.assert_not_called()


def test_mapping_reports_failed_generation_after_clearing(world):
    # arrange
    world.map("Calc10ms", world.tasks.Add("Existing"), 0)
    world.ecu.GenerateRunnableSchedule.side_effect = lambda: False

    # act
    result = vc.generate_runnable_mapping()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "SYSTEMDESK_OPERATION_FAILED"
    assert "could not generate" in result["message"]
    assert "already deleted" in result["message"]
    assert world.tasks.Elements == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"tasks": [{"name": "Task10ms", "priority": 1}]},
        {"mappings": [{"task": "T", "component": "C", "runnable": "R", "rte_event": "E"}]},
    ],
)
def test_mapping_removed_custom_arguments_rejected_by_schema(arguments):
    with pytest.raises(ValidationError):
        validate_args(vc.generate_runnable_mapping, **arguments)


# --- configure_rte_interventions ---

def test_interventions_default_creates_a_service_for_each_point(world):
    # arrange
    world.rti.possible = [
        Point(world.component, "RpIn", "Value", READ, runnable="A"),
        Point(world.component, "RpIn", "Value", READ, runnable="B"),
        Point(world.component, "PpOut", "Result", WRITE),
        Point(world.service_component, "Pp_Service_output", "Result", WRITE),
        IrvPoint(world.component),
    ]

    # act
    result = vc.configure_rte_interventions()

    # assert
    assert result["status"] == "success"
    assert len(result["points_created"]) == 3
    assert result["services_created"] == [
        "Ctrl_PpOut_OutputService",
        "Ctrl_RpIn_InputService",
        "Ctrl_RpIn_InputService1",
    ]
    assert result["access"] == "dap"
    assert result["enabled"] is True
    services = {s.ShortName: s for s in world.rti.InterventionServices.Elements}
    assert (services["Ctrl_RpIn_InputService"].InputType, services["Ctrl_RpIn_InputService"].OutputType) == (DAP_SERVICE, NO_SERVICE)
    assert (services["Ctrl_PpOut_OutputService"].InputType, services["Ctrl_PpOut_OutputService"].OutputType) == (NO_SERVICE, DAP_SERVICE)
    assert len(services["Ctrl_RpIn_InputService"].points) == 1
    assert len(services["Ctrl_RpIn_InputService1"].points) == 1
    assert len(world.rti.possible) == 2
    assert world.rti.delayed_mode_calls == ["enter", "exit"]


def test_interventions_rte_service_port_access(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]

    # act
    result = vc.configure_rte_interventions(access="rte_service_port")

    # assert
    assert result["access"] == "rte_service_port"
    assert world.rti.InterventionServices.Elements[0].InputType == RTE_SERVICE_PORT


def test_interventions_selected_points_only(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ), Point(world.component, "PpOut", "Result", WRITE)]

    # act
    result = vc.configure_rte_interventions(
        intervention_points=[InterventionPointSpec(component="Ctrl", port="PpOut", data_element="Result")])

    # assert
    assert result["services_created"] == ["Ctrl_PpOut_OutputService"]
    assert [p.ShortName for p in world.rti.possible] == ["Rte_IRead_Ctrl_Run_RpIn_Value"]


def test_interventions_are_idempotent(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]

    # act
    vc.configure_rte_interventions()
    second = vc.configure_rte_interventions()

    # assert
    assert second["points_created"] == []
    assert second["services_created"] == []
    assert len(world.rti.InterventionServices.Elements) == 1


def test_interventions_do_not_reuse_existing_service(world):
    # arrange
    service = world.rti.InterventionServices.AddNew("Ctrl_RpIn_InputService")
    service.InputType = DAP_SERVICE
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ, runnable="New")]

    # act
    result = vc.configure_rte_interventions(access="rte_service_port")

    # assert
    assert result["points_created"] == ["Rte_IRead_Ctrl_New_RpIn_Value"]
    assert result["services_created"] == ["Ctrl_RpIn_InputService1"]
    assert service.InputType == DAP_SERVICE
    assert service.points == []
    created_service = next(
        service for service in world.rti.InterventionServices.Elements
        if service.ShortName == "Ctrl_RpIn_InputService1"
    )
    assert created_service.InputType == RTE_SERVICE_PORT
    assert len(created_service.points) == 1


def test_interventions_map_existing_unmapped_points(world):
    # arrange
    unmapped = Point(world.component, "RpIn", "Value", READ)
    world.rti.existing = [unmapped]

    # act
    result = vc.configure_rte_interventions(
        intervention_points=[InterventionPointSpec(component="Ctrl", port="RpIn", data_element="Value")])

    # assert
    assert result["points_created"] == []
    assert result["services_created"] == ["Ctrl_RpIn_InputService"]
    assert unmapped.service is world.rti.InterventionServices.Elements[0]


def test_interventions_requested_point_that_already_exists_is_not_an_error(world):
    # arrange
    world.rti.existing = [Point(world.component, "RpIn", "Value", READ, service=Service("S"))]

    # act
    result = vc.configure_rte_interventions(
        intervention_points=[InterventionPointSpec(component="Ctrl", port="RpIn", data_element="Value")])

    # assert
    assert result["status"] == "success"
    assert result["points_created"] == []


@pytest.mark.parametrize("component, port", [("Ctrl", "Nope"), ("Rte", "Pp_Service_output")])
def test_interventions_unknown_point_rejected_without_changes(world, component, port):
    # arrange
    world.rti.possible = [
        Point(world.component, "RpIn", "Value", READ),
        Point(world.service_component, "Pp_Service_output", "Value", WRITE),
    ]

    # act
    result = vc.configure_rte_interventions(intervention_points=[
        InterventionPointSpec(component="Ctrl", port="RpIn", data_element="Value"),
        InterventionPointSpec(component=component, port=port, data_element="Value"),
    ])

    # assert
    assert result["error_code"] == "AUTOSAR_ELEMENT_NOT_FOUND"
    assert f"{component}/{port}/Value" in result["message"]
    assert world.rti.existing == []
    assert world.rti.InterventionEnableCodeGeneration is False
    assert world.rti.delayed_mode_calls == ["enter", "exit"]


def test_interventions_duplicate_points_rejected(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]
    spec = InterventionPointSpec(component="Ctrl", port="RpIn", data_element="Value")

    # act
    result = vc.configure_rte_interventions(intervention_points=[spec, spec])

    # assert
    assert result["error_code"] == "INVALID_ARGUMENTS"


def test_interventions_schema_declares_unique_points_and_allows_empty_list():
    hint = typing.get_type_hints(vc.configure_rte_interventions, include_extras=True)["intervention_points"]
    array_schema = next(s for s in TypeAdapter(hint).json_schema()["anyOf"] if s.get("type") == "array")
    assert array_schema["uniqueItems"] is True
    assert "minItems" not in array_schema


def test_interventions_enable_false_disables_code_generation(world):
    # arrange
    world.rti.InterventionEnableCodeGeneration = True

    # act
    result = vc.configure_rte_interventions(enable=False)

    # assert
    assert result["enabled"] is False
    assert world.rti.InterventionEnableCodeGeneration is False


def test_interventions_empty_list_only_sets_enable(world):
    # arrange
    world.rti.InterventionEnableCodeGeneration = True
    unmapped = Point(world.component, "PpOut", "Result", WRITE)
    world.rti.existing = [unmapped]
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]

    # act
    result = vc.configure_rte_interventions(intervention_points=[], enable=False)

    # assert
    assert result["status"] == "success"
    assert result["points_created"] == []
    assert result["services_created"] == []
    assert result["enabled"] is False
    assert len(world.rti.possible) == 1
    assert world.rti.InterventionServices.Elements == []
    assert unmapped.service is None
    assert world.rti.kind_options == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"access": "both"},
        {"intervention_points": [{"component": "C", "port": "P"}]},
        {"intervention_points": [{"component": "C", "port": "P", "data_element": "D", "extra": 1}]},
    ],
)
def test_interventions_invalid_arguments_rejected_by_schema(arguments):
    with pytest.raises(ValidationError):
        validate_args(vc.configure_rte_interventions, **arguments)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("timeout", [0, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(tool, timeout):
    with pytest.raises(ValidationError):
        validate_args(tool, timeout=timeout)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("vecu_name", ["", "1Ecu", "My Ecu"])
def test_invalid_vecu_name_rejected_by_schema(tool, vecu_name):
    with pytest.raises(ValidationError):
        validate_args(tool, vecu_name=vecu_name)
