---
name: smoke-test-vecu-creation
description: Smoke test to create a V-ECU, and export it with SystemDesk MCP.
disable-model-invocation: true
---
# Goal

Use the SystemDesk MCP Server to perform an end-to-end smoke test by importing the supplied model, creating a V-ECU, and confirming a successful FMU export.

# Rules

* Create a short-named directory under `%TEMP%`; keep all test-created files there and remove it after the test, including on failure.
* Do NOT inspect the files in this workspace, only rely on the information provided in this prompt and by the MCP server.
* Do NOT read the repository memory or state from previous sessions. Fail the run, if it happens.
* Do NOT write to the repository memory. Fail the run and delete the created memory, if it happens.

# Instructions

1. Import `./assets/smoke-test-vecu-gen.arxml`, create a V-ECU and export it as an FMU.
2. Close SystemDesk.

# Output

Report whether the V-ECU was created, the FMU export path and result, any discrepancies, and whether cleanup succeeded.
