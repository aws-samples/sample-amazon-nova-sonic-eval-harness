---
name: config-generator
description: Generate valid Nova Sonic test configuration JSON files from natural language descriptions. Invoke when asked to create, modify, clone, or adapt test configs — reads the full TestConfig schema and model alias registry to produce correct JSON with proper system prompts, evaluation criteria, and tool definitions.
---

# Config Generator Skill

Generate valid Nova Sonic test configuration JSON files from natural language descriptions.

## Project Context

This is the Nova Sonic Test Harness — an end-to-end test framework for Amazon Nova Sonic (speech-to-speech model) via AWS Bedrock. Configs are JSON files that define a test scenario: the voice agent's prompt, the simulated user's persona, tool definitions, and evaluation criteria.

## When to Use

Activate this skill when the user asks to:
- Create a new test config or scenario for Nova Sonic
- Generate a configuration for testing a voice agent
- Set up a new evaluation scenario
- Modify, clone, or adapt an existing config (e.g., enable audio, change input mode, add tools)
- Create a config variant for a specific evaluation type (audio-text eval, batch testing, etc.)
- Create a config file for any purpose related to this project

## Instructions

1. Ask the user to describe the test scenario if not already provided. Key details to gather:
   - What kind of voice agent is being tested (customer support, healthcare, travel, etc.)
   - Whether tools/function calling is needed
   - How many conversation turns
   - Any specific evaluation criteria

2. Read the full `TestConfig` schema from `.kiro/skills/config-generator/schema_reference.md` and use it to generate a valid JSON config.

3. Read model aliases from `.kiro/skills/config-generator/model_aliases.md`. Always use aliases (e.g., `"claude-haiku"`, `"nova-sonic"`) — never use raw Bedrock model IDs in configs.

4. Follow these rules for the generated config:
   - `sonic_system_prompt`: Write as instructions for a phone agent (spoken, not text-based). Include role, company/context, rules, response style. Tell it to keep responses to 1-2 short sentences. Tell it never to use markdown.
   - `user_system_prompt`: Give the simulated user a name and persona. Describe their goal, emotional state, conversation style. Tell them to keep messages to 1-2 sentences and speak naturally. Do NOT include `[HANGUP]` instructions — the harness appends this automatically.
   - `sonic_voice_id`: Pick from `matthew`, `tiffany`, `amy` based on persona fit.
   - `evaluation_criteria`: Always include `user_goal`, `assistant_objective`, and relevant `evaluation_aspects`.
   - If tools are needed, include `sonic_tool_config` with proper Bedrock `toolSpec` format and add `"Tool Usage"` to evaluation aspects.

5. Validate the generated config:
   - Ensure all tool names in `sonic_tool_config` match any `tool_registry_module` handlers.
   - Ensure `evaluation_aspects` only reference built-in metrics: `Goal Achievement`, `Response Accuracy`, `Tool Usage`, `Conversation Flow`, `Voice Formatting`, `System Prompt Compliance`. (Legacy alias `Response Quality` is accepted as `Response Accuracy`.)
   - Verify `interaction_mode` is `"scripted"` if and only if `scripted_messages` is non-empty.

6. Save the config to `configs/<test_name>.json` unless the user specifies a different path.

7. Alternatively, the user can run the CLI generator: `python -m utils.generate_config "description" --tools --turns 8`
