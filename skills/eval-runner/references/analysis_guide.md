# Analysis and Improvement Guide

## Purpose

After running evaluations, use this guide to interpret results, identify root causes of failures, and make targeted improvements to the agent's system prompt and tool definitions.

## Step 1: Locate Results

Results are stored at:
- `results/sessions/<session_id>/` — individual session
- `results/batches/<batch_id>/` — batch run summary

Key files per session:
- `chat/conversation.txt` — human-readable transcript
- `evaluation/llm_judge_evaluation.json` — structured verdicts per metric
- `logs/interaction_log.json` — full structured log with tool call params and results

## Step 2: Read the Evaluation

The `llm_judge_evaluation.json` contains:
- `overall_rating`: "PASS" or "FAIL"
- `pass_rate`: fraction of metrics that passed
- `metric_verdicts`: per-metric breakdown with reasoning
- `strengths` and `weaknesses`: summary lists

Start with `weaknesses` to identify what failed, then drill into `metric_verdicts` for the specific rubric questions that returned "NO".

## Step 3: Common Failure Patterns and Fixes

### Wrong dates in tool parameters

**Symptom:** Tool calls use dates like `2024-03-01` when the conversation is in 2026.

**Root cause:** Nova Sonic doesn't know today's date unless told.

**Fix:** Add to system prompt:
```
== Today's date ==
Today is YYYY-MM-DD. Use this to resolve relative dates.
Next month means YYYY-MM-01.
```

Also add `"description": "Move-in date in YYYY-MM-DD format, e.g. 2026-06-01"` to date parameters in tool specs.

### Hallucinated facts (addresses, prices, tour slots)

**Symptom:** Agent states facts that no tool returned (e.g., invents an address).

**Root cause:** The tool wasn't available in the config, or the prompt didn't enforce tool-backed truthfulness strongly enough.

**Fix:**
1. Add the missing tool to `sonic_tool_config.tools`
2. Add explicit rule: "Never state [X] without calling [tool] first"
3. Add fallback instruction: "If you do not have a tool result, say you don't have that detail on the call"

### Redundant/duplicate tool calls

**Symptom:** Same tool called twice with identical parameters in one turn.

**Root cause:** Model generates parallel tool calls and duplicates one.

**Fix:** Add to system prompt:
```
Do not call the same tool twice with identical parameters.
If you already have the result, use it directly.
```

Also add to the tool's description: "Do not call this twice for the same parameters."

### Repetitive/formulaic responses

**Symptom:** Every response ends with the same suggestion (e.g., "Would you like to book a tour?").

**Root cause:** Prompt says to "move toward a next step" without qualifying when.

**Fix:** Replace with:
```
Follow the caller's lead. Only suggest next steps when the caller signals
readiness or when their current questions are fully answered. Do not
repeatedly suggest the same action.
```

### Tool not called when it should be

**Symptom:** Agent answers from memory instead of calling the appropriate tool.

**Root cause:** Prompt doesn't have a strong enough "required preamble pair" for that tool.

**Fix:** Add explicit preamble pair:
```
Required preamble pairs:
- "Checking [X] now." -> tool_name
```

And add: "Never answer [topic] without a fresh tool result in this turn."

### Agent goes off-scope

**Symptom:** Agent tries to help with topics it should redirect (maintenance, legal, etc.).

**Root cause:** Scope boundaries not explicit enough.

**Fix:** Add explicit redirect list:
```
Refer [topic1], [topic2], [topic3] to the team.
Use get_property_contact_details when an office phone is needed.
```

## Step 4: Improvement Workflow

1. **Read the evaluation** — identify which metrics failed and why
2. **Read the conversation transcript** — find the exact turn where the failure occurred
3. **Check the interaction log** — look at tool_input params and tool_result values
4. **Identify the root cause** — match to a pattern above
5. **Edit the system prompt** — make targeted additions (don't rewrite everything)
6. **Edit tool descriptions** — add format hints or usage constraints
7. **Re-run the same config** — verify the fix
8. **Run the full batch** — check for regressions

## Step 5: Batch Analysis

For batch runs, check `results/batches/<batch_id>/batch_summary.json`:

```json
{
  "evaluation_summary": {
    "pass_rate": 0.8,
    "metric_pass_rates": {
      "Goal Achievement": { "passed": 4, "total": 5, "rate": 0.8 },
      "Tool Usage": { "passed": 3, "total": 5, "rate": 0.6 }
    }
  }
}
```

Focus on the lowest `metric_pass_rates` first. Then drill into the failing sessions to find common patterns.

## Step 6: Statistical Confidence

Single runs can be noisy. Use `--repeat N` to get statistical confidence:

```bash
python main.py --config configs/leasing/leasing_availability.json --repeat 5 --parallel 3
```

A metric that fails 4/5 times is a real problem. A metric that fails 1/5 times may be noise or an edge case.

## Step 7: Carrying Fixes to the Live Agent

After improving the eval config's system prompt, port the changes back to the live agent:

1. **Date grounding** — inject today's date dynamically at session start
2. **Truthfulness rules** — add to the production prompt
3. **Conversation flow** — update the "follow the caller's lead" guidance
4. **Tool descriptions** — update the tool schemas in the live agent's tool definitions

The eval harness prompt and the live agent prompt should stay in sync. The eval prompt may have additional test-specific context (like explicit date), but the core rules should match.
