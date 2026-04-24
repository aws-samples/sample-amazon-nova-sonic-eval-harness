---
name: tool-builder
description: Generate tool handler Python modules for Nova Sonic test scenarios. Invoke when asked to create mock tool implementations, build tool handlers, or generate tool definitions — produces correctly structured async modules with ToolRegistry decorators, realistic mock data, and matching toolSpec JSON for configs.
---

# Tool Builder Skill

Generate tool handler modules for Nova Sonic test scenarios.

## Project Context

This is the Nova Sonic Test Harness. Tools let Nova Sonic call external functions (e.g., order lookup, appointment booking) during a conversation. Tool handlers are Python modules with a `ToolRegistry` that maps tool names to async handler functions. The harness loads them dynamically via the config's `tool_registry_module` field.

## When to Use

Activate this skill when the user asks to:
- Create tool handlers for a test scenario
- Build mock tool implementations
- Generate tool definitions for a config

## Instructions

1. Ask the user what tools are needed if not already described. Gather:
   - Tool names and what they do
   - Input parameters for each tool
   - What kind of mock data to return

2. Read the reference implementation pattern from `.kiro/skills/tool-builder/tool_pattern.md` and follow it exactly.

3. The module must include:
   - A module-level `registry = ToolRegistry()` instance
   - `@registry.tool("toolName")` decorated async handler for each tool
   - Realistic mock data as module-level constants (dicts, lists)
   - Simulated API latency (`await asyncio.sleep(0.3-0.5)`)
   - Error handling for missing/invalid inputs
   - A standalone `async def main()` test function with `if __name__ == "__main__"` block

4. Also generate the matching `sonic_tool_config` JSON block that goes in the test config file. Tool names and parameter schemas must match exactly between the Python handler and the JSON config.

5. Save the tool module to `tools/<module_name>.py` (or `examples/<module_name>.py` for example implementations).

6. Update the test config's `tool_registry_module` field to point to the new module (e.g., `"tools.flight_booking_tools"`).

7. Verify the tool works by checking that `tools/tool_registry.py` exists and the import path is correct for the project structure.
