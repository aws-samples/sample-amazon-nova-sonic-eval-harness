# Skill: Run Evaluation for a Voice Agent

Use this skill when you need to evaluate a Nova Sonic voice agent given its system prompt and tools, or when adapting an existing voice agent application for evaluation with this harness.

## When to Use

- User provides a system prompt and tool definitions and wants to evaluate the agent
- User points to an existing voice agent codebase and wants to run it through the eval harness
- User wants to create evaluation configs, tool handler modules, or datasets for a voice agent
- User wants to improve an agent based on evaluation results

## Process Overview

1. **Understand the agent** — Read the system prompt, tool definitions, and mock data
2. **Create the tool handler module** — Adapt tools to the `ToolRegistry` pattern
3. **Create evaluation configs** — Build JSON configs covering different scenarios (happy path, edge cases, error handling, off-topic, multi-tool flows, etc.)
4. **Run the evaluation** — Execute and collect results
5. **Analyze and improve** — Read results, identify failures, fix prompt/tools

## Expected Output

After completing this skill, you will have:

1. **A set of JSON config files** covering different evaluation scenarios for the agent (e.g., `configs/<agent>/scenario_a.json`, `configs/<agent>/scenario_b.json`, etc.). Each config targets a distinct conversation flow or edge case.

2. **Sample commands to run the configs:**

```bash
# Run a single scenario
python main.py --config configs/<agent>/<scenario>.json

# Run all scenarios in the agent's config directory (batch)
python main.py --scenarios-dir configs/<agent> --parallel 2

# Repeat a scenario for statistical confidence
python main.py --config configs/<agent>/<scenario>.json --repeat 5 --parallel 3
```

After creating the configs, always print the batch command so the user can run all scenarios at once:

```
To run all scenarios as a batch:
python main.py --scenarios-dir configs/<agent> --parallel 2
```

3. **How to review results:**

- **Inline (terminal):** Results are printed at the end of each run with PASS/FAIL verdicts and per-metric breakdowns. Session logs are saved to `results/sessions/<session_id>/`.
- **Streamlit dashboard:** For a richer view with charts, conversation playback, and batch comparison:

```bash
streamlit run evaluation/evaluation_dashboard.py
```

The dashboard lets you browse batches, drill into individual sessions, view full conversation transcripts with tool calls, see per-metric pass rates, and compare runs side-by-side.

## Reference Files

- #[[file:skills/eval-runner/references/tool_module_guide.md]] — How to create a tool handler module
- #[[file:skills/eval-runner/references/config_guide.md]] — How to write evaluation configs
- #[[file:skills/eval-runner/references/analysis_guide.md]] — How to analyze results and improve the agent
