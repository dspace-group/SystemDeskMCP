---
name: smoke-test
agent: agent
description: End-to-end smoke test for the SystemDesk MCP server.
---

# Goal

Use the SystemDesk MCP Server to perform an end-to-end smoke test, ensuring that the core functionalities are working as expected.

# Rules

* Work in %TEMP% for any file or directory creation. Do not create files or directories outside of this location.
* Do NOT inspect the files in this workspace, only rely on the information provided in this prompt and by the MCP server.
* Do NOT read the repository memory or state from previous sessions. Fail the run, if it happens.
* Do NOT write to the repository memory. Fail the run and delete the created memory, if it happens.

# Instructions

1. Always clean up any created directories or files after the test execution, regardless of the test results.
2. SystemDesk.exe should not be running before the test starts.
3. Use the tools provided by the SystemDesk MCP server to validate the file `tests\smoke-test.arxml`, then close SystemDesk.
   The following results are expected from the validation:
     - 1 Error
       - `/SWA/B/InternalBehavior/DataReceivedEvent` is missing the "DataIref".
     - 2 Warnings
       - `/SWA/A/InternalBehavior/Runnable` is not triggered by any event.
       - `/SWA/RootComposition/AssemblySwConnector_022a6a18ca20b376e84badaf962bf0ac` is an incompatible connection.
4. After validation, use the tools provided by the SystemDesk MCP server to get the status of SystemDesk and whether a project is opened. The expected results are:
   - SystemDesk is running.
   - A project is opened.
5. SystemDesk.exe should not be running after the test finishes.

Fail the test if any of the expected results are not met, or if SystemDesk.exe is running at the wrong times.

# Output

Provide a summary of the results. Be pedantic if something seems wrong. Be concise.
