---
name: smoke-test-all
description: Runs all smoke-test-* skills sequentially and reports a combined result.
disable-model-invocation: true
---
# Goal

Run every `smoke-test-*` skill against the SystemDesk MCP Server and report a combined result.

# Rules

* Only read the `SKILL.md` files of the sibling `smoke-test-*` skills. Do NOT inspect their assets or any other files in this workspace.
* Do NOT read the repository memory or state from previous sessions. Fail the run, if it happens.
* Do NOT write to the repository memory. Fail the run and delete the created memory, if it happens.
* Run exactly one smoke test at a time; never start the next one before the previous one has finished.
* Do NOT pass results of earlier smoke tests to later ones.

# Instructions

1. Find all `smoke-test-*/SKILL.md` files in the sibling directories of this skill, excluding `smoke-test-all`, and sort them alphabetically by skill name. Fail if none are found.
2. For each smoke test, launch one subagent using the custom agent named exactly `smoke-test-runner`. Do NOT use the default agent. If `smoke-test-runner` is unavailable, fail the run without starting any smoke test. Pass:
   - The body of its `SKILL.md` verbatim, without the frontmatter.
   - The absolute path of its skill directory.
3. Treat a missing or ambiguous verdict as `FAIL`. Treat a report of unavailable SystemDesk MCP tools as `FAIL`, and include it in the output.
4. On `FAIL`, ensure SystemDesk is closed using the SystemDesk MCP server and stop. Mark all remaining smoke tests as `NOT RUN`.

# Output

Provide a table with one row per smoke test: name, result (`PASS`, `FAIL`, or `NOT RUN`), and a one-line summary. State the overall result, which is `PASS` only if all smoke tests ran and passed. For a failed smoke test, include its report and cleanup status. Be pedantic if something seems wrong. Be concise.
