# Skills

Reusable step-by-step guides for common tasks in this project. Each skill contains project-specific rules, validation steps, and reference files that produce better results than working from scratch.

These skills work with any AI coding agent that supports skill/instruction files, including **Kiro** and **Claude Code**.

## Available Skills

### eval-runner — Run Evaluation for a Voice Agent

**When to use:** Evaluating a Nova Sonic voice agent given its system prompt and tools, or adapting an existing voice agent application for evaluation with this harness.

**Entry point:** `skills/eval-runner/skill.md`

**What it covers:**
1. Creating a tool handler module (`ToolRegistry` pattern)
2. Writing evaluation configs (system prompt best practices, tool specs, user sim prompts)
3. Running evaluations (single, batch, dataset-driven, repeated)
4. Analyzing results and improving the agent (common failure patterns and fixes)

**Reference files:**
- `skills/eval-runner/tool_module_guide.md` — Tool module creation and adaptation
- `skills/eval-runner/config_guide.md` — Config structure, prompt tips, running commands
- `skills/eval-runner/analysis_guide.md` — Reading results, root-cause analysis, improvement workflow

## Iteration Example

After running an evaluation, you can ask the agent to read the results and iterate on the system prompt and tools. The key rule: **the eval config's system prompt and tool definitions must stay in sync with the live agent** — changes made during iteration should be applied to both.

### Sample prompts for iteration:

```
Read the eval results in results/sessions/<session_id>/ and tell me what failed.
Then update the system prompt and tool definitions in the config to fix the issues.
```

```
The Tool Usage metric is failing because the agent uses wrong date formats.
Update the tool descriptions in configs/order_status/order_status_normal.json
to include format hints, and add date grounding to the system prompt.
```

```
Run the batch again after the fixes:
python main.py --scenarios-dir configs/order_status --parallel 2

Then compare the new results to the previous batch and summarize what improved.
```

### What the agent should do during iteration:

1. Read the evaluation output (`evaluation/llm_judge_evaluation.json` or batch summary)
2. Identify which metrics failed and the reasoning
3. Read the conversation transcript to find the exact failure turn
4. Apply targeted fixes to the **system prompt** and/or **tool descriptions** in the config
5. Ensure the same prompt and tool changes are reflected in the live agent code (if applicable)
6. Re-run the scenario to verify the fix
7. Run the full batch to check for regressions

