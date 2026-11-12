"""Tests for the V-ECU configuration tools (no SystemDesk installation required)."""
# pylint: disable=protected-access,too-few-public-methods,invalid-name
# The fakes mirror the SystemDesk COM object model and need no docstrings.
# pylint: disable=missing-function-docstring,missing-class-docstring,too-many-instance-attributes
# pylint: disable=too-many-arguments,too-many-positional-arguments

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp import common, runtime
from systemdesk_mcp.tools import vecu_configuration as vc
from systemdesk_mcp.tools.vecu_configuration import (
    InterventionPointSpec,
    OsTaskSpec,
    RunnableMappingSpec,
)
from tests.helpers import validate_args

READ, WRITE = 0, 2
OS_PATH = "/ArPackage/EcuConfiguration_Test_Ecu/Os"
RTE_PATH = "/ArPackage/EcuConfiguration_Test_Ecu/Rte"


# --------------------------------------------------------------------------------------------------
# Fakes of the SystemDesk object model
# --------------------------------------------------------------------------------------------------

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
        self.InputType = 0
        self.OutputType = 0
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
        self.app = app
        self.tasks = Collection(Task)
        self.os_cfg = SimpleNamespace(OsTasks=self.tasks, AUTOSARPathName=OS_PATH)
        self.activations = []
        self.rte_cfg = SimpleNamespace(
            AUTOSARPathName=RTE_PATH,
            RteSwComponentInstances=Collection(_instance),
            RteBswModuleInstances=Collection(),
            RteOsInteractions=Collection(elements=[SimpleNamespace(
                ShortName="RteOsInteraction", RteUsedOsActivations=SimpleNamespace(Elements=self.activations))]),
            RunBswPlugin=MagicMock(side_effect=self._update_rte_configuration),
        )
        self.plugin_errors = []
        self.events = [
            _event("InitEvent", "INIT-EVENT", "Init"),
            _event("Calc10ms", "TIMING-EVENT", "Calc", 0.01),
            _event("Corr10ms", "TIMING-EVENT", "Corr", 0.01),
            _event("ModeEvent", "SWC-MODE-SWITCH-EVENT", "OnMode"),
        ]
        self.component = _component("Ctrl", self.events)
        self.service_component = _component("Rte", [], "SERVICE-SW-COMPONENT-TYPE")
        self.rti = RteInterventions()
        self.rule_results = []
        self.ecu = MagicMock()
        self.ecu.Name = "EcuConfiguration_Test_Ecu"
        self.ecu.GetModuleConfigurationByDefinitionName.side_effect = self._module
        self.ecu.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements = [
            self.component, self.service_component]
        self.ecu.RteInterventions = self.rti
        self.ecu.Validate.ResultStructure.RuleResults = self.rule_results
        self.modules = {"Os": self.os_cfg, "Rte": self.rte_cfg}
        self.vecus = [SimpleNamespace(Name=name, EcuConfiguration=self.ecu) for name in vecu_names]
        app.ActiveProject.VEcus.Elements = self.vecus
        app.ActiveProject.VEcus.Item.side_effect = self._vecu
        app.BatchMode = False

    def _module(self, name, _inactive):
        return self.modules.get(name)

    def _vecu(self, name):
        found = next((v for v in self.vecus if v.Name == name), None)
        if found is None:
            raise RuntimeError("not found")
        return found

    def _update_rte_configuration(self, command):
        assert command == "Update Rte Configuration"
        assert self.app.BatchMode is True
        if self.rte_cfg.RteSwComponentInstances.Item("Ctrl") is None:
            self.rte_cfg.RteSwComponentInstances.Add("Ctrl")
        for task in self.tasks.Elements:
            if not any(a.RteActivationOsTaskRef is task for a in self.activations):
                self.activations.append(SimpleNamespace(
                    ShortName=task.ShortName, RteActivationOsTaskRef=task, RteExpectedTickDuration=-1.0))
        errors = [SimpleNamespace(MessageIdentifier=i, MessageText=t) for i, t in self.plugin_errors]
        return SimpleNamespace(ErrorMessages=SimpleNamespace(Count=len(errors), Elements=errors))

    def map(self, event_name, task, position):
        instance = self.rte_cfg.RteSwComponentInstances.Item("Ctrl") or self.rte_cfg.RteSwComponentInstances.Add("Ctrl")
        mapping = instance.RteEventToTaskMappings.Add(event_name)
        mapping.RteEventRef = next(e for e in self.events if e.ShortName == event_name)
        mapping.RteMappedToTaskRef = task
        mapping.RtePositionInTask = position
        return mapping

    def rule(self, rule_id, text, path, severity="Error"):
        entity = SimpleNamespace(AUTOSARPathName=path) if path else SimpleNamespace()
        self.rule_results.append(SimpleNamespace(
            Severity=severity, RuleId=rule_id, Entity=entity,
            Message=SimpleNamespace(Text=text, ChildMessages=[])))


@pytest.fixture(name="world")
def _world(app):
    return World(app)


def _mapping_spec(task="Task10ms", event="Calc10ms", runnable="Calc", position=None, component="Ctrl"):
    return RunnableMappingSpec(task=task, component=component, runnable=runnable, rte_event=event, position=position)


# --------------------------------------------------------------------------------------------------
# Common preconditions
# --------------------------------------------------------------------------------------------------

def _call(tool):
    if tool is vc.configure_runnable_mapping:
        return tool(tasks=[OsTaskSpec(name="Task10ms", priority=1)])
    return tool()


TOOLS = [vc.get_vecu_configuration, vc.configure_runnable_mapping, vc.configure_rte_interventions]


@pytest.mark.parametrize("tool", TOOLS)
def test_not_started(tool):
    # arrange
    runtime._application = None

    # act
    result = _call(tool)

    # assert
    assert result["status"] == "error"
    assert result["error_code"] == "SYSTEMDESK_NOT_RUNNING"


@pytest.mark.parametrize("tool", TOOLS)
def test_no_project_open(app, tool):
    # arrange
    app.ActiveProject = None

    # act
    result = _call(tool)

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"


@pytest.mark.parametrize("tool", TOOLS)
def test_no_vecu_in_project(app, tool):
    # arrange
    World(app, vecu_names=())

    # act
    result = _call(tool)

    # assert
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "does not contain a V-ECU" in result["message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_unknown_vecu_name_lists_available_vecus(app, tool):
    # arrange
    World(app, vecu_names=("Controller", "Test_Ecu"))

    # act
    result = tool(vecu_name="Missing") if tool is not vc.configure_runnable_mapping else tool(
        tasks=[OsTaskSpec(name="Task10ms", priority=1)], vecu_name="Missing")

    # assert
    assert result["error_code"] == "VECU_NOT_FOUND"
    assert "Controller, Test_Ecu" in result["message"]


@pytest.mark.parametrize("tool", TOOLS)
def test_single_vecu_is_used_by_default(app, tool):
    # arrange
    World(app, vecu_names=("Controller",))

    # act
    result = _call(tool)

    # assert
    assert result["status"] == "success"
    assert result["vecu_name"] == "Controller"


@pytest.mark.parametrize("tool", TOOLS)
def test_several_vecus_without_name_require_selection(app, tool):
    # arrange
    World(app, vecu_names=("Controller", "Test_Ecu"))

    # act
    result = _call(tool)

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
    result = vc.get_vecu_configuration(vecu_name="Test_Ecu", sections=["os_tasks"])

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
    result = _call(tool)

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "UNEXPECTED_ERROR"


# --------------------------------------------------------------------------------------------------
# get_vecu_configuration
# --------------------------------------------------------------------------------------------------

def test_get_returns_all_sections_by_default(world):
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
    assert list(result) == ["status", "vecu_name", "runnables", "os_tasks", "rte_intervention_points", "rte_interventions"]
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


def test_get_only_requested_sections(world):
    # act
    result = vc.get_vecu_configuration(sections=["os_tasks", "runnables"])

    # assert
    assert set(result) == {"status", "vecu_name", "runnables", "os_tasks"}
    world.rte_cfg.RunBswPlugin.assert_not_called()


def test_get_runnables_without_rte_module(world):
    # arrange
    del world.modules["Rte"]

    # act
    result = vc.get_vecu_configuration(sections=["runnables"])

    # assert
    assert [r["mapped_task"] for r in result["runnables"]] == [None, None, None, None]


def test_get_runnables_skips_component_without_internal_behaviors(world):
    # arrange
    composition = SimpleNamespace(ShortName="Comp", TypeTref=SimpleNamespace(AutosarType="COMPOSITION-SW-COMPONENT-TYPE"))
    world.ecu.EcuExtractSystem.RootSwCompositionPrototype.SoftwareCompositionTref.Components.Elements.append(composition)

    # act
    result = vc.get_vecu_configuration(sections=["runnables"])

    # assert
    assert result["status"] == "success"
    assert {r["component"] for r in result["runnables"]} == {"Ctrl"}


def test_get_os_tasks_without_os_module(world):
    # arrange
    del world.modules["Os"]

    # act
    result = vc.get_vecu_configuration(sections=["os_tasks"])

    # assert
    assert result["os_tasks"] == []


def test_get_intervention_points_are_unique_and_skip_irv_and_service_components(world):
    # arrange
    world.rti.possible = [
        Point(world.component, "RpIn", "Value", READ, runnable="A"),
        Point(world.component, "RpIn", "Value", READ, runnable="B"),
        Point(world.component, "PpOut", "Result", WRITE),
        Point(world.service_component, "Pp_Service_output", "Result", WRITE),
        IrvPoint(world.component),
    ]

    # act
    result = vc.get_vecu_configuration(sections=["rte_intervention_points"])

    # assert
    assert result["rte_intervention_points"] == [
        {"component": "Ctrl", "port": "RpIn", "data_element": "Value"},
        {"component": "Ctrl", "port": "PpOut", "data_element": "Result"},
    ]
    assert world.rti.kind_options == [3]


def test_get_interventions_report_service_and_access(world):
    # arrange
    dap = Service("InService")
    dap.InputType = 1
    port = Service("OutService")
    port.OutputType = 2
    world.rti.existing = [
        Point(world.component, "RpIn", "Value", READ, service=dap),
        Point(world.component, "PpOut", "Result", WRITE, service=port),
        Point(world.component, "PpOut", "Other", WRITE),
    ]

    # act
    result = vc.get_vecu_configuration(sections=["rte_interventions"])

    # assert
    assert [(e["service"], e["access"]) for e in result["rte_interventions"]] == [
        ("InService", "dap"), ("OutService", "rte_service_port"), (None, None)]


@pytest.mark.parametrize("sections", [[], ["unknown"]])
def test_get_invalid_sections_rejected_by_schema(sections):
    with pytest.raises(ValidationError):
        validate_args(vc.get_vecu_configuration, sections=sections)


# --------------------------------------------------------------------------------------------------
# configure_runnable_mapping
# --------------------------------------------------------------------------------------------------

def test_mapping_creates_tasks_runs_update_and_maps_events(world):
    # act
    result = vc.configure_runnable_mapping(
        tasks=[OsTaskSpec(name="Task10ms", period_ms=10, priority=10), OsTaskSpec(name="TaskInit", priority=20)],
        mappings=[
            _mapping_spec("TaskInit", "InitEvent", "Init", 0),
            _mapping_spec("Task10ms", "Calc10ms", "Calc", 0),
            _mapping_spec("Task10ms", "Corr10ms", "Corr", 1),
        ],
    )

    # assert
    assert result["status"] == "success"
    assert result["tasks"] == [
        {"name": "Task10ms", "period_ms": 10.0, "priority": 10},
        {"name": "TaskInit", "period_ms": None, "priority": 20},
    ]
    assert [(m["task"], m["rte_event"], m["position"]) for m in result["mappings"]] == [
        ("TaskInit", "InitEvent", 0), ("Task10ms", "Calc10ms", 0), ("Task10ms", "Corr10ms", 1)]
    assert result["schedule_generated"] is True
    assert result["errors"] == []
    task10 = world.tasks.Item("Task10ms")
    assert task10.OsTaskActivation == 1
    assert task10.OsTaskPeriod == pytest.approx(0.01)
    assert {a.ShortName: a.RteExpectedTickDuration for a in world.activations} == {
        "Task10ms": pytest.approx(0.01), "TaskInit": -1.0}
    mapping = world.rte_cfg.RteSwComponentInstances.Item("Ctrl").RteEventToTaskMappings.Item("Calc10ms")
    assert mapping.RteMappedToTaskRef is task10
    assert mapping.RteEventRef.ShortName == "Calc10ms"
    world.rte_cfg.RunBswPlugin.assert_called_once_with("Update Rte Configuration")
    assert world.app.BatchMode is False


def test_mapping_updates_existing_task_and_keeps_period_if_omitted(world):
    # arrange
    task = world.tasks.Add("Task10ms")
    task.OsTaskPeriod = 0.01
    task.OsTaskActivation = 3

    # act
    result = vc.configure_runnable_mapping(tasks=[OsTaskSpec(name="Task10ms", priority=7)])

    # assert
    assert result["tasks"] == [{"name": "Task10ms", "period_ms": 10.0, "priority": 7}]
    assert task.OsTaskActivation == 3
    assert len(world.tasks.Elements) == 1


def test_mapping_without_position_appends_after_rte_and_bsw_mappings(world):
    # arrange
    task = world.tasks.Add("Task10ms")
    world.map("Calc10ms", task, 0)
    bsw_mapping = SimpleNamespace(RteBswMappedToTaskRef=task, RteBswPositionInTask=4)
    world.rte_cfg.RteBswModuleInstances.Elements.append(
        SimpleNamespace(RteBswEventToTaskMappings=SimpleNamespace(Elements=[bsw_mapping])))

    # act
    result = vc.configure_runnable_mapping(mappings=[_mapping_spec("Task10ms", "Corr10ms", "Corr")])

    # assert
    assert result["mappings"][0]["position"] == 5


def test_mapping_without_position_keeps_position_in_same_task(world):
    # arrange
    task = world.tasks.Add("Task10ms")
    world.map("Calc10ms", task, 3)

    # act
    first = vc.configure_runnable_mapping(mappings=[_mapping_spec("Task10ms", "Calc10ms", "Calc")])
    second = vc.configure_runnable_mapping(mappings=[_mapping_spec("Task10ms", "Calc10ms", "Calc")])

    # assert
    assert first["mappings"][0]["position"] == 3
    assert second["mappings"][0]["position"] == 3


def test_mapping_moves_existing_mapping_to_other_task(world):
    # arrange
    old_task = world.tasks.Add("Old")
    new_task = world.tasks.Add("New")
    mapping = world.map("Calc10ms", old_task, 0)

    # act
    vc.configure_runnable_mapping(mappings=[_mapping_spec("New", "Calc10ms", "Calc", 2)])

    # assert
    instance = world.rte_cfg.RteSwComponentInstances.Item("Ctrl")
    assert instance.RteEventToTaskMappings.Elements == [mapping]
    assert mapping.RteMappedToTaskRef is new_task
    assert mapping.RtePositionInTask == 2


def test_mapping_is_idempotent(world):
    # arrange
    tasks = [OsTaskSpec(name="Task10ms", period_ms=10, priority=10)]
    mappings = [_mapping_spec("Task10ms", "Calc10ms", "Calc", 0)]

    # act
    first = vc.configure_runnable_mapping(tasks=tasks, mappings=mappings)
    second = vc.configure_runnable_mapping(tasks=tasks, mappings=mappings)

    # assert
    assert first == second
    assert len(world.tasks.Elements) == 1
    assert len(world.rte_cfg.RteSwComponentInstances.Item("Ctrl").RteEventToTaskMappings.Elements) == 1


def test_mapping_returns_os_and_rte_validation_errors_only(world):
    # arrange
    world.rule("R808", "Rte - period mismatch", RTE_PATH)
    world.rule("R870", "Os - missing counter", OS_PATH + "/OsCounter")
    world.rule("R919", "code files missing", "/ArPackage/EcuConfiguration_Test_Ecu/Sab")
    world.rule("R999", "warning on Rte", RTE_PATH, severity="Warning")
    world.rule("R937", "no path", None)

    # act
    result = vc.configure_runnable_mapping(tasks=[OsTaskSpec(name="Task20ms", period_ms=20, priority=5)])

    # assert
    assert result["errors"] == [
        {"message": "R808: Rte - period mismatch", "element": RTE_PATH},
        {"message": "R870: Os - missing counter", "element": OS_PATH + "/OsCounter"},
    ]
    world.ecu.Validate.Do.assert_called_once()


@pytest.mark.parametrize(
    "spec, code, text",
    [
        (_mapping_spec(component="Nope"), "AUTOSAR_ELEMENT_NOT_FOUND", "no SW component 'Nope'"),
        (_mapping_spec(event="Missing"), "AUTOSAR_ELEMENT_NOT_FOUND", "no RTE event 'Missing'"),
        (_mapping_spec(runnable="Wrong"), "INVALID_ARGUMENTS", "starts runnable 'Calc', not 'Wrong'"),
        (_mapping_spec(task="NoTask"), "AUTOSAR_ELEMENT_NOT_FOUND", "'NoTask' does not exist"),
    ],
)
def test_mapping_rejects_unknown_elements_without_changes(world, spec, code, text):
    # arrange
    world.tasks.Add("Task10ms")

    # act
    result = vc.configure_runnable_mapping(tasks=[OsTaskSpec(name="New", priority=1)], mappings=[spec])

    # assert
    assert result["error_code"] == code
    assert text in result["message"]
    assert world.tasks.Item("New") is None
    world.rte_cfg.RunBswPlugin.assert_not_called()


@pytest.mark.usefixtures("world")
def test_mapping_task_given_in_request_does_not_need_to_exist():
    # act
    result = vc.configure_runnable_mapping(
        tasks=[OsTaskSpec(name="Task10ms", period_ms=10, priority=1)], mappings=[_mapping_spec()])

    # assert
    assert result["status"] == "success"


@pytest.mark.parametrize(
    "tasks, mappings",
    [
        ([OsTaskSpec(name="T", priority=1), OsTaskSpec(name="T", priority=2)], []),
        ([OsTaskSpec(name="Task10ms", priority=1)], [_mapping_spec(position=0), _mapping_spec(position=1)]),
    ],
)
def test_mapping_rejects_duplicates(world, tasks, mappings):
    # act
    result = vc.configure_runnable_mapping(tasks=tasks, mappings=mappings)

    # assert
    assert result["error_code"] == "INVALID_ARGUMENTS"
    assert world.tasks.Elements == []


def test_mapping_reports_update_rte_configuration_errors(world):
    # arrange
    world.plugin_errors = [("Error(1,2,3)", "Update failed")]

    # act
    result = vc.configure_runnable_mapping(tasks=[OsTaskSpec(name="T", priority=1)])

    # assert
    assert result["error_code"] == "SYSTEMDESK_OPERATION_FAILED"
    assert "Error(1,2,3): Update failed" in result["message"]
    assert world.app.BatchMode is False


@pytest.mark.parametrize("module", ["Os", "Rte"])
def test_mapping_requires_os_and_rte_modules(world, module):
    # arrange
    del world.modules[module]

    # act
    result = vc.configure_runnable_mapping(tasks=[OsTaskSpec(name="Task10ms", priority=1)])

    # assert
    assert result["error_code"] == "AUTOSAR_ELEMENT_NOT_FOUND"
    assert module in result["message"]


@pytest.mark.parametrize(
    "task",
    [
        {"name": "1Task", "priority": 1},
        {"name": "Task", "priority": -1},
        {"name": "Task", "priority": 1, "period_ms": 0},
        {"name": "Task", "priority": 1, "period_ms": 0.00009},
        {"name": "Task", "priority": 1, "period_ms": 3_600_001},
        {"name": "Task", "priority": 1, "unknown": 1},
        {"name": "Task"},
    ],
)
def test_mapping_invalid_task_rejected_by_schema(task):
    with pytest.raises(ValidationError):
        validate_args(vc.configure_runnable_mapping, tasks=[task])


@pytest.mark.parametrize(
    "mapping",
    [
        {"task": "T", "component": "C", "runnable": "R", "rte_event": "E", "position": -1},
        {"task": "T", "component": "C", "runnable": "R"},
        {"task": "T", "component": "C/x", "runnable": "R", "rte_event": "E"},
    ],
)
def test_mapping_invalid_mapping_rejected_by_schema(mapping):
    with pytest.raises(ValidationError):
        validate_args(vc.configure_runnable_mapping, mappings=[mapping])


@pytest.mark.parametrize("period_ms", [0.0001, 3_600_000])
def test_mapping_task_period_bounds_accepted_by_schema(period_ms):
    validate_args(vc.configure_runnable_mapping, tasks=[{"name": "Task", "priority": 1, "period_ms": period_ms}])


def test_mapping_tasks_and_mappings_are_optional_in_schema():
    validate_args(vc.configure_runnable_mapping, tasks=[{"name": "Task", "priority": 1}])
    validate_args(vc.configure_runnable_mapping, mappings=[
        {"task": "T", "component": "C", "runnable": "R", "rte_event": "E"}])


@pytest.mark.parametrize("argument", ["tasks", "mappings"])
def test_mapping_empty_list_rejected_by_schema(argument):
    with pytest.raises(ValidationError):
        validate_args(vc.configure_runnable_mapping, **{argument: []})


@pytest.mark.parametrize("arguments", [{}, {"tasks": None, "mappings": None}, {"tasks": [], "mappings": []}])
def test_mapping_without_tasks_and_mappings_is_rejected(world, arguments):
    # act
    result = vc.configure_runnable_mapping(**arguments)

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "INVALID_ARGUMENTS"
    assert "at least one of tasks and mappings" in result["message"]
    world.rte_cfg.RunBswPlugin.assert_not_called()


# --------------------------------------------------------------------------------------------------
# configure_rte_interventions
# --------------------------------------------------------------------------------------------------

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
    assert (services["Ctrl_RpIn_InputService"].InputType, services["Ctrl_RpIn_InputService"].OutputType) == (1, 0)
    assert (services["Ctrl_PpOut_OutputService"].InputType, services["Ctrl_PpOut_OutputService"].OutputType) == (0, 1)
    assert len(services["Ctrl_RpIn_InputService"].points) == 1
    assert len(services["Ctrl_RpIn_InputService1"].points) == 1
    assert len(world.rti.possible) == 2


def test_interventions_rte_service_port_access(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]

    # act
    result = vc.configure_rte_interventions(access="rte_service_port")

    # assert
    assert result["access"] == "rte_service_port"
    assert world.rti.InterventionServices.Elements[0].InputType == 2


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
    service.InputType = 1
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ, runnable="New")]

    # act
    result = vc.configure_rte_interventions(access="rte_service_port")

    # assert
    assert result["points_created"] == ["Rte_IRead_Ctrl_New_RpIn_Value"]
    assert result["services_created"] == ["Ctrl_RpIn_InputService1"]
    assert service.InputType == 1
    assert service.points == []
    created_service = next(
        service for service in world.rti.InterventionServices.Elements
        if service.ShortName == "Ctrl_RpIn_InputService1"
    )
    assert created_service.InputType == 2
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


def test_interventions_duplicate_points_rejected(world):
    # arrange
    world.rti.possible = [Point(world.component, "RpIn", "Value", READ)]
    spec = InterventionPointSpec(component="Ctrl", port="RpIn", data_element="Value")

    # act
    result = vc.configure_rte_interventions(intervention_points=[spec, spec])

    # assert
    assert result["error_code"] == "INVALID_ARGUMENTS"


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
    extra = {"tasks": [], "mappings": []} if tool is vc.configure_runnable_mapping else {}
    with pytest.raises(ValidationError):
        validate_args(tool, timeout=timeout, **extra)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("vecu_name", ["", "1Ecu", "My Ecu"])
def test_invalid_vecu_name_rejected_by_schema(tool, vecu_name):
    extra = {"tasks": [], "mappings": []} if tool is vc.configure_runnable_mapping else {}
    with pytest.raises(ValidationError):
        validate_args(tool, vecu_name=vecu_name, **extra)
