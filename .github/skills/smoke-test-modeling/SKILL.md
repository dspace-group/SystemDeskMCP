---
name: smoke-test-modeling
description:  End-to-end smoke test covering SystemDesk startup, modeling, validation, and shutdown.
disable-model-invocation: true
---
# Goal
Use the SystemDesk MCP Server to verify that SystemDesk can be started, used for modeling and validation, and shut down successfully.

# Rules

* Work in %TEMP% for any file or directory creation. Do not create files or directories outside of this location.
* Do NOT inspect the files in this workspace, only rely on the information provided in this prompt and by the MCP server.
* Do NOT read the repository memory or state from previous sessions. Fail the run, if it happens.
* Do NOT write to the repository memory. Fail the run and delete the created memory, if it happens.
* Relative file paths should be used with respect to this skill file.

# Instructions

1. Always clean up any created directories or files after the test execution, regardless of the test results.
2. SystemDesk.exe should not be running before the test starts.
3. Copy `./assets/smoke-test.arxml` to %TEMP%, import the copy, and create a composition at `/Compositions/SmokeComposition` containing `/SWA/A` and `/SWA/B`.
4. Export the imported copy. The export must succeed, and the copy must still contain `/SWA/A` and `/SWA/B` but not `SmokeComposition`. Check this by inspecting the exported file.
5. Use the tools provided by the SystemDesk MCP server to validate the project.
   The following results are expected from the validation:
     - 1 Error
       - `/SWA/B/InternalBehavior/DataReceivedEvent` is missing the "DataIref".
     - 6 Warnings
       - `/SWA/A/InternalBehavior/Runnable` is not triggered by any event.
       - `/SWA/RootComposition/AssemblySwConnector_022a6a18ca20b376e84badaf962bf0ac` is an incompatible connection.
       - `/SWA/RootComposition` has a warning for the missing ComSpec init value of unconnected receiver element `B.RPort1.E1`.
       - `/Compositions/SmokeComposition` has three warnings for the missing ComSpec init value of unconnected receiver element `B.RPort1.E1`, `B.RPort.E1` and `B.RPort.E2`.
6. Use the tools provided by the SystemDesk MCP server to connect open ports in the composition created in step 3 `/Compositions/SmokeComposition`. It is only allowed to use the interface types and only one connection can be created for each open port.
   The following results are expected:
   - `status="success"`.
   - `connection_count=2` and exactly two entries in `connections`, which connect A.PPort to B.RPort1 and A.PPort1 to B.RPort.
7. After connecting ports, use the tools provided by the SystemDesk MCP server to get the status of SystemDesk and whether a project is opened. The expected results are:
   - SystemDesk is running.
   - A project is opened.
   - The project is modified.
8. Save the project. Afterwards, get the status of SystemDesk again. The expected results are:
   - A project is opened.
   - The project is not modified.
9. Close SystemDesk.
10. SystemDesk.exe should not be running after the test finishes. Allow a grace period for it to close properly.

Fail the test if any of the expected results are not met, or if SystemDesk.exe is running at the wrong times.

# Output

Provide a summary of the results. Be pedantic if something seems wrong. Be concise.
