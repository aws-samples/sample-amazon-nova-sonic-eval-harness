---
name: eval-analyzer
description: Analyze evaluation results from Nova Sonic test runs and provide actionable recommendations. Invoke when asked to analyze test results, debug failures, compare batches, or improve prompts based on evaluation feedback — reads batch summaries, session evaluations, and interaction logs to identify failure patterns and suggest fixes.
---

# Eval Analyzer Skill

Analyze evaluation results from Nova Sonic test runs and provide actionable recommendations.

## Project Context

This is the Nova Sonic Test Harness. After each test run, a binary LLM judge evaluates the conversation on metrics like Goal Achievement, Tool Usage, Response Quality, and Conversation Flow. Results are stored per-session and aggregated into batch summaries.

## When to Use

Activate this skill when the user asks to:
- Analyze test results or evaluation data
- Understand why tests are failing
- Compare batch results
- Improve system prompts based on evaluation feedback
- Debug evaluation failures

## Instructions

1. Read the data formats documentation from `.kiro/skills/eval-analyzer/data_formats.md` to understand the JSON structures for batch indexes, batch summaries, session evaluations, and interaction logs.

2. Start by loading the batch index to understand what data is available:
   - Read `results/batches/batch_index.json` for the list of batches
   - Read `results/batches/<batch_id>/batch_summary.json` for batch-level stats

3. Analyze failure patterns:
   - Identify which metrics fail most often (`evaluation_summary.metric_pass_rates`)
   - Look for co-failure correlations (metrics that fail together)
   - Group failures by config name to find problematic scenarios
   - Read individual session evaluations for the worst failures

4. For each failing session, read:
   - `results/sessions/<session_id>/evaluation/llm_judge_evaluation.json` — judge verdicts and reasoning
   - `results/sessions/<session_id>/logs/interaction_log.json` — the actual conversation
   - `results/sessions/<session_id>/config/test_config.json` — the config used

5. Generate recommendations:
   - System prompt improvements based on failure reasoning
   - Tool definition adjustments if Tool Usage fails
   - Evaluation criteria refinements if metrics seem miscalibrated
   - Config parameter tweaks (max_turns, temperature, etc.)

6. When comparing batches:
   - Compare pass rates and per-metric rates between batches
   - Identify regressions (metrics that got worse)
   - Identify improvements (metrics that got better)
   - Correlate changes with config differences

7. Present findings as a structured analysis with:
   - Summary statistics
   - Top failure patterns with examples
   - Specific, actionable recommendations
   - Suggested config or prompt changes (provide the actual text)
