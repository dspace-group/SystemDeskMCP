---
name: smoke-test-vecu-configuration
description: Smoke-test V-ECU runnable mappings and RTE interventions with SystemDesk MCP.
disable-model-invocation: true
---
# Goal

Use the SystemDesk MCP Server to verify that runnable mappings and RTE interventions can be configured for a V-ECU.

# Rules

* Work in %TEMP% for any file or directory creation. Do not create files or directories outside of this location.
* Do NOT inspect files in this workspace; only use the specified ARXML as an import input and rely on the MCP server for results.
* Do NOT read repository memory or state from previous sessions. Fail the run if this happens.
* Do NOT write to repository memory. Fail the run and delete the created memory if this happens.

# Instructions

1. Always clean up any created directories or files after the test execution, regardless of the test results.
2. Import `./assets/smoke-test-vecu-gen.arxml` in a new SystemDesk project.
3. Create a V-ECU called `TestVecu` without creating runnable mappings or RTE interventions.
4. **Initial-state verification:** Read the V-ECU configuration without specifying the V-ECU name. The read must succeed and return `vecu_name: TestVecu`; if multiple V-ECUs exist, selection will be required and the verification fails. Verify:
   - There is exactly one V-ECU, named `TestVecu`.
   - `os_tasks` is empty.
   - No runnable has a mapped task.
   - No RTE interventions are configured.
   Fail before continuing if any assertion is not met.
5. Map component `A`, runnable `SWA_Runnable`, event `SWA_TEvent` to a new task named `Task10ms`, with period 10ms and priority 8.
   - The two expected Os validation errors (missing `OsCounter` and `OSDEFAULTAPPMODE`) do not fail the test.
6. Configure an RTE intervention for component `A`, port `PPort`, data element `E1`, with access: `dap`.
7. Generate BSW for `TestVecu`.
8. **Post-generation verification:** Read the full configuration of `TestVecu` and verify:
   - OS tasks: `Task10ms` (10 ms, priority 8), `EcuM_StartupTwoTask` (non-cyclic), and `MainFunctionTask2_1ms` (1 ms, priority 13).
   - The mapped runnable is component `A`, runnable `SWA_Runnable`, timing event `SWA_TEvent`, mapped to the `Task10ms`.
   - `A.PPort1.E1` remains an available, unmapped intervention point.
   - The configured intervention is `Rte_IWrite_A_SWA_Runnable_PPort_E1`, service `A_PPort_OutputService`, access `dap`.
   Fail the test if any expected result is missing or different.
9. Close SystemDesk.

Fail the test if any expected configuration result is not met.

# Output

Provide a concise summary of the initial state, configured mapping and intervention, validation errors, and cleanup. Be pedantic if something seems wrong.
