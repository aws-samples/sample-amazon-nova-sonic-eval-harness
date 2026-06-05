# Tool Module Guide

## Purpose

The eval harness needs a Python module that implements the agent's tools using the `ToolRegistry` decorator pattern. This module simulates the backend services the voice agent calls during a conversation.

## Module Structure

Create the file at `examples/<agent_name>_tools.py`. It must:

1. Import and instantiate `ToolRegistry`
2. Expose a module-level `registry` attribute
3. Register each tool with `@registry.tool("tool_name")` matching the exact `toolSpec.name` in the config
4. Each handler is an `async` function accepting `tool_input: Dict[str, Any]` and returning a `str` or `dict`

## Template

```python
"""
<Agent Name> Tools for Evaluation

Tool handlers for the <agent name> evaluation scenarios.
Provides <N> tools with simulated data matching the agent's expected responses.
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.tool_registry import ToolRegistry

# Module-level registry (loaded by main.py via tool_registry_module config)
registry = ToolRegistry()

# ---------------------------------------------------------------------------
# Mock data store
# ---------------------------------------------------------------------------

_MOCK_DATA = {
    # Populate with realistic data matching what the real backend returns
}

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@registry.tool("myToolName")
async def my_tool(tool_input: Dict[str, Any]) -> str:
    """Description of what this tool does."""
    await asyncio.sleep(0.3)  # Simulate API latency
    param = tool_input.get("param_name")
    # Return a compacted text summary (preferred for voice agents)
    # or a dict (auto-serialized to JSON for Nova Sonic)
    return (
        f"Tool result summary for myToolName.\n"
        f"key_field={param}\n"
        f"data=..."
    )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

async def main():
    """Standalone test for tool handlers."""
    print(f"Registered tools: {', '.join(registry.list_tools())}")
    print(f"Total: {len(registry.list_tools())}")
    result = await registry.execute("myToolName", {"param_name": "test"})
    print(f"Result: {result}")

if __name__ == "__main__":
    asyncio.run(main())
```

## Key Rules

1. **Tool names must exactly match** the `toolSpec.name` values in the evaluation config JSON
2. **Handlers must be async** — use `await asyncio.sleep()` to simulate realistic latency
3. **String results are auto-wrapped** — the `ToolRegistry.execute()` method wraps string returns as `{"result": "..."}` for Nova Sonic JSON compatibility
4. **Mock data must be realistic** — use the same data the real agent would see so evaluation results are meaningful
5. **Return compacted text for voice agents** — Nova Sonic works best with pre-compacted text summaries rather than raw JSON blobs. Include instructions like "Speak these exact amounts only" or "Do not read internal refs aloud"

## Adapting from Existing Agent Code

When the agent already has tool implementations (e.g., Strands `@tool` functions, MCP tools, or Lambda handlers):

1. **Copy the mock data** — Reuse the same simulated data store from the existing agent
2. **Copy the response format** — Match the exact output format the agent's system prompt expects
3. **Adapt the decorator** — Change from `@tool` (Strands) or other patterns to `@registry.tool("name")`
4. **Change the signature** — All handlers take `tool_input: Dict[str, Any]` as the single parameter; extract named params with `.get()`

### Example: Strands @tool → ToolRegistry

**Before (Strands):**
```python
@tool
def get_pricing(property_id: int, unit_space_id: int, move_in_date: str) -> str:
    ...
```

**After (ToolRegistry):**
```python
@registry.tool("get_pricing")
async def get_pricing(tool_input: Dict[str, Any]) -> str:
    property_id = tool_input.get("property_id")
    unit_space_id = tool_input.get("unit_space_id")
    move_in_date = tool_input.get("move_in_date")
    await asyncio.sleep(0.3)
    ...
```

## Validation

Run the module standalone to verify all tools register and execute:

```bash
python -m examples.<agent_name>_tools
```

Expected output:
```
Registered tools: tool1, tool2, tool3, ...
Total: N
Result: {...}
```
