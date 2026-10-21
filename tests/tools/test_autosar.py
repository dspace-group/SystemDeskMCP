"""Tests for autosar behavior."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from systemdesk_mcp.tools import autosar
from systemdesk_mcp import common
from tests.helpers import VALID_ABSOLUTE_DIRS, validate_args


def test_import_autosar_file_not_found():
    # act
    result = autosar.import_autosar_file(file_path="C:\\nonexistent\\file.arxml")

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "FILE_NOT_FOUND"


def test_import_autosar_file_success(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "valid_model.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    active_project = MagicMock()
    import_export_file = MagicMock()
    import_export_file.Import.return_value = True
    active_project.ImportExportFiles.Add.return_value = import_export_file
    app.ActiveProject = active_project

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "success"
    assert result["imported_file"] == str(arxml_file)
    assert "imported successfully" in result["message"].lower()
    active_project.ImportExportFiles.Add.assert_called_once_with(str(arxml_file))
    import_export_file.Import.assert_called_once()
    import_export_file.Delete.assert_not_called()


def test_import_autosar_no_project_open(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "sample.arxml"
    arxml_file.write_text("<AUTOSAR/>")
    app.ActiveProject = None

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


def test_validate_autosar_with_error(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False
    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Missing reference"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert result["warning_count"] == 0
    assert result["total_count"] == 1
    assert "R101" in result["report"]
    assert "Missing reference" in result["report"]
    assert "/R344_invalid/SWC" in result["report"]


def test_validate_autosar_with_warning(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = True
    rule_result = MagicMock()
    rule_result.Severity = "Warning"
    rule_result.RuleId = "R210"
    rule_result.Message.Text = "Signal has no default value"
    rule_result.Entity.AUTOSARPathName = "/System/Signals/VehicleSpeed"
    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert result["warning_count"] == 1
    assert result["total_count"] == 1
    assert "R210" in result["report"]
    assert "Signal has no default value" in result["report"]
    assert "/System/Signals/VehicleSpeed" in result["report"]


def test_validate_autosar_with_child_messages_markdown(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False

    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Main information\nwith multiple lines"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"

    child_message1 = MagicMock()
    child_message1.Text = "Level 1 info"
    rule_result.Message.ChildMessages = [child_message1]

    child_message2 = MagicMock()
    child_message2.Text = "Level 2 info"
    child_message1.ChildMessages = [child_message2]

    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert "Main information<br>with multiple lines<br>Level 1 info<br>Level 2 info" in result["report"]


def test_validate_autosar_with_child_messages_json(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = False

    rule_result = MagicMock()
    rule_result.Severity = "Error"
    rule_result.RuleId = "R101"
    rule_result.Message.Text = "Main information\nwith multiple lines"
    rule_result.Entity.AUTOSARPathName = "/R344_invalid/SWC"

    child_message1 = MagicMock()
    child_message1.Text = "Level 1 info"
    rule_result.Message.ChildMessages = [child_message1]

    child_message2 = MagicMock()
    child_message2.Text = "Level 2 info"
    child_message1.ChildMessages = [child_message2]

    app.ActiveProject.Validate.ResultStructure.RuleResults = [rule_result]

    # act
    result = autosar.validate_autosar(output_format=autosar.ValidateOutputFormat.JSON)

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 1
    assert result["messages"] == [
        {
            "severity": "Error",
            "rule": "R101",
            "description": "Main information\nwith multiple lines",
            "description_details": [
                {
                    "text": "Level 1 info",
                    "children": [
                        {
                            "text": "Level 2 info",
                            "children": []
                        }
                    ]
                }
            ],
            "element": "/R344_invalid/SWC",
        }
    ]


def test_validate_autosar_with_no_results(app):
    # arrange
    app.ActiveProject.Validate.Do.return_value = True
    app.ActiveProject.Validate.ResultStructure.RuleResults = []

    # act
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert result["warning_count"] == 0
    assert result["total_count"] == 0
    assert "No validation issues found" in result["report"]


def test_validate_autosar_no_project_open(app):
    # arrange
    app.ActiveProject = None

    # act
    result = autosar.validate_autosar()

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "NO_PROJECT_OPEN"
    assert "no project is currently open" in result["message"].lower()


def test_import_autosar_file_schema_error(app, tmp_path):
    # arrange
    arxml_file = tmp_path / "invalid_model.arxml"
    arxml_file.write_text("<AUTOSAR><InvalidElement/></AUTOSAR>")
    active_project = MagicMock()
    import_export_file = MagicMock()
    import_export_file.Import.return_value = False
    active_project.ImportExportFiles.Add.return_value = import_export_file
    app.ActiveProject = active_project

    app.Messages.Elements = [MagicMock(), MagicMock()]
    msg = app.Messages.Elements[-1]
    msg.MessageIdentifier = 'Info(93,600,17)'
    msg.Severity = 'Info'
    msg.MessageText = 'Preparing'
    msg.Children.Elements = [MagicMock(), MagicMock()]
    msg.Children.Elements[0].Severity = 'Info'
    msg.Children.Elements[0].MessageText = 'Validating'
    msg.Children.Elements[0].Children.Elements = [MagicMock()]
    msg.Children.Elements[0].Children.Elements[0].Severity = 'Error'
    msg.Children.Elements[0].Children.Elements[0].MessageText = "Invalid element"
    msg.Children.Elements[0].Children.Elements[0].Children.Elements = []
    msg.Children.Elements[1].Severity = 'Error'
    msg.Children.Elements[1].MessageText = 'Import failed'
    msg.Children.Elements[1].Children.Elements = []

    # act
    result = autosar.import_autosar_file(file_path=str(arxml_file))

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == "IMPORT_REJECTED"
    assert result["details"] == "[Info] Preparing\n  [Info] Validating\n    [Error] Invalid element\n  [Error] Import failed"
    active_project.ImportExportFiles.Add.assert_called_once_with(str(arxml_file))
    import_export_file.Import.assert_called_once()
    import_export_file.Delete.assert_called_once()


def test_import_autosar_wrong_extension_rejected_by_schema(tmp_path):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path=str(tmp_path / "file.xml"))


@pytest.mark.parametrize(
    "file_path",
    [d.rstrip("\\/") + "\\file.arxml" for d in VALID_ABSOLUTE_DIRS]
    + ["C:\\Projects\\file.ARXML", "C:\\Projects\\file.ArXmL", "C:\\Projects\\my.file.arxml"],
)
def test_import_autosar_absolute_path_accepted_by_schema(file_path):
    validate_args(autosar.import_autosar_file, file_path=file_path)


@pytest.mark.parametrize(
    "file_path",
    [
        "file.arxml",
        "Projects\\file.arxml",
        ".\\file.arxml",
        "..\\Projects\\file.arxml",
        "\\Projects\\file.arxml",
        "/Projects/file.arxml",
        "C:file.arxml",
        "1:\\Projects\\file.arxml",
        "\\\\\\share\\file.arxml",
        "C:\\Projects\\file.xml",
        "C:\\Projects\\file.arxml.bak",
        "C:\\Projects\\filearxml",
    ],
)
def test_import_autosar_invalid_path_rejected_by_schema(file_path):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path=file_path)


@pytest.mark.parametrize("timeout", [0, -1, common.MAX_TIMEOUT_SECONDS + 1])
def test_timeout_out_of_range_rejected_by_schema(timeout):
    with pytest.raises(ValidationError):
        validate_args(autosar.import_autosar_file, file_path="C:\\a.arxml", timeout=timeout)
    with pytest.raises(ValidationError):
        validate_args(autosar.validate_autosar, timeout=timeout)


def composition_model(project):
    """Install a stateful mock of the documented composition COM operations."""
    elements = {}

    def add_element(path, autosar_type):
        element = MagicMock(ShortName=path.rsplit("/", 1)[1],
                            AUTOSARPathName=path, AutosarType=autosar_type)
        elements[path] = element

        def delete():
            for candidate in list(elements):
                if candidate == path or candidate.startswith(path + "/"):
                    del elements[candidate]
            return True

        element.Delete.side_effect = delete
        element.ArPackages.AddNew.side_effect = lambda name: add_element(
            path + "/" + name, "AR-PACKAGE",
        )
        element.Elements.AddNewCompositionSwComponentType.side_effect = lambda name: add_element(
            path + "/" + name, "COMPOSITION-SW-COMPONENT-TYPE",
        )
        element.Components.Elements = []

        def add_component(component):
            names = {prototype.ShortName for prototype in element.Components.Elements}
            name = component.ShortName
            suffix = 1
            while name in names:
                name = component.ShortName + str(suffix)
                suffix += 1
            prototype = MagicMock(ShortName=name, TypeTref=component)
            element.Components.Elements.append(prototype)
            return prototype

        element.AddSwComponentType.side_effect = add_component
        return element

    project.GetElementsByARPath.side_effect = lambda path: (
        [elements[path]] if path in elements else []
    )
    project.RootAutosar.ArPackages.AddNew.side_effect = lambda name: add_element(
        "/" + name, "AR-PACKAGE",
    )
    add_element("/SWA", "AR-PACKAGE")
    add_element("/SWA/A", "APPLICATION-SW-COMPONENT-TYPE")
    add_element("/SWA/B", "APPLICATION-SW-COMPONENT-TYPE")
    return elements


def test_create_composition_success(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    package = elements["/SWA"]
    component = elements["/SWA/A"]

    # act
    result = autosar.create_composition(
        composition_path="/SWA/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result == {
        "status": "success", "path": "/SWA/Top",
        "components": [{"name": "A", "type": "/SWA/A"}],
    }
    package.Elements.AddNewCompositionSwComponentType.assert_called_once_with("Top")
    composition = elements["/SWA/Top"]
    composition.AddSwComponentType.assert_called_once_with(component)
    composition.Delete.assert_not_called()


def test_create_composition_nested_packages_and_unique_names(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    paths = ["/SWA/A", "/SWA/B", "/SWA/A"]

    # act
    result = autosar.create_composition(
        composition_path="/Generated/Nested/Top", component_paths=paths,
    )

    # assert
    assert result["status"] == "success"
    assert result["path"] == "/Generated/Nested/Top"
    assert [item["type"] for item in result["components"]] == paths
    assert len({item["name"] for item in result["components"]}) == len(paths)
    assert elements["/Generated"].AutosarType == "AR-PACKAGE"
    assert elements["/Generated/Nested"].AutosarType == "AR-PACKAGE"
    composition = elements[result["path"]]
    assert [call.args[0] for call in composition.AddSwComponentType.call_args_list] == [
        elements[path] for path in paths
    ]
    for path in paths:
        elements[path].Delete.assert_not_called()


@pytest.mark.parametrize("component_type", ["APPLICATION-SW-COMPONENT-TYPE", "SERVICE-SW-COMPONENT-TYPE",])
def test_create_composition_accepts_different_component_types(app, component_type):
    # arrange
    elements = composition_model(app.ActiveProject)
    elements["/SWA/A"].AutosarType = component_type

    # act
    result = autosar.create_composition(
        composition_path="/SWA/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result["status"] == "success"


@pytest.mark.parametrize("failure,code,path", [
    ("missing", "AUTOSAR_ELEMENT_NOT_FOUND", "/Missing/Component"),
    ("ambiguous", "AMBIGUOUS_AUTOSAR_PATH", "/SWA/A"),
    ("type", "INVALID_AUTOSAR_ELEMENT_TYPE", "/SWA/B"),
    ("occupied", "AUTOSAR_ELEMENT_ALREADY_EXISTS", "/Generated/Top"),
    ("parent", "INVALID_AUTOSAR_ELEMENT_TYPE", "/Generated"),
    ("ambiguous_parent", "AMBIGUOUS_AUTOSAR_PATH", "/Generated"),
])
def test_create_composition_prechecks_do_not_modify_model(app, failure, code, path):
    # arrange
    project = app.ActiveProject
    elements = composition_model(project)
    if failure == "type":
        elements[path].AutosarType = "SENDER-RECEIVER-INTERFACE"
    elif failure in ("occupied", "parent"):
        elements[path] = MagicMock(AutosarType="APPLICATION-SW-COMPONENT-TYPE")
    elif failure in ("ambiguous", "ambiguous_parent"):
        lookup = project.GetElementsByARPath.side_effect
        project.GetElementsByARPath.side_effect = lambda candidate: (
            [MagicMock(), MagicMock()] if candidate == path else lookup(candidate)
        )
    before = dict(elements)
    selected = ["/SWA/A", path] if failure in {"missing", "type"} else ["/SWA/A"]

    # act
    result = autosar.create_composition(
        composition_path="/Generated/Top", component_paths=selected,
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "permanent"
    assert result["error_code"] == code
    assert path in result["message"]
    assert elements == before
    project.RootAutosar.ArPackages.AddNew.assert_not_called()
    for element in before.values():
        element.ArPackages.AddNew.assert_not_called()
        element.Elements.AddNewCompositionSwComponentType.assert_not_called()
        element.Delete.assert_not_called()


def test_create_composition_deletes_composition_on_failure(app):
    # arrange
    elements = composition_model(app.ActiveProject)
    # Without a ShortName property, the model's AddSwComponentType raises.
    elements["/SWA/A"] = MagicMock(spec=["AutosarType"], AutosarType="APPLICATION-SW-COMPONENT-TYPE")

    # act
    result = autosar.create_composition(
        composition_path="/Generated/Nested/Top", component_paths=["/SWA/A"],
    )

    # assert
    assert result["status"] == "error"
    assert result["error_type"] == "transient"
    assert result["error_code"] == "UNEXPECTED_ERROR"
    assert "/Generated/Nested/Top" not in elements


@pytest.mark.parametrize("valid_path", ["/SWA/A", "/a_1/B_", "/SWA/" + "A" * 128])
def test_create_composition_accepts_valid_path_schema(valid_path):
    # act / assert
    validate_args(autosar.create_composition, composition_path=valid_path, component_paths=[valid_path])


@pytest.mark.parametrize("invalid_path", [
    "", "/", "SWA/A", "A", "/SWA", "/SWA//A", "/SWA/A/", "/SWA/../A",
    "C:/SWA/A", "\\SWA\\A", "/SWA/A B", "/SWA/1A", "/SWA/*", "/SWA/A\n",
    "/_SWA/A", "/SWA/_A", "/SWA/" + "A" * 129,
])
@pytest.mark.parametrize("parameter", ["composition_path", "component_paths"])
def test_create_composition_rejects_invalid_path_schema(invalid_path, parameter):
    # arrange
    arguments = {"composition_path": "/SWA/Top", "component_paths": ["/SWA/A"]}
    arguments[parameter] = [invalid_path] if parameter == "component_paths" else invalid_path

    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar.create_composition, **arguments)


@pytest.mark.parametrize("paths", [[], [None], [42], "/SWA/A"])
def test_create_composition_requires_nonempty_path_list(paths):
    # act / assert
    with pytest.raises(ValidationError):
        validate_args(autosar.create_composition, composition_path="/SWA/Top", component_paths=paths)
