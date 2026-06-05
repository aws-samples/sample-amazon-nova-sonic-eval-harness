# Evaluation Config Guide

## Purpose

Evaluation configs define a test scenario: the agent's system prompt, available tools, the simulated user's behavior, and evaluation criteria. Each config produces one conversation session that gets judged by the LLM evaluator.

## Config Location

Place configs at `configs/<agent_name>/<scenario_name>.json`.

## Required Fields

```json
{
  "test_name": "short_snake_case_name",
  "description": "One-line description of what this scenario tests",
  "sonic_model_id": "nova-sonic",
  "sonic_system_prompt": "The agent's full system prompt (see System Prompt section below)",
  "sonic_inference_config": {
    "max_tokens": 1024,
    "temperature": 0.7,
    "top_p": 0.9
  },
  "sonic_tool_config": {
    "tools": [ /* toolSpec array */ ],
    "tool_choice": { "auto": {} }
  },
  "user_model_id": "claude-haiku",
  "user_system_prompt": "Instructions for the simulated user (see User Sim section below)",
  "user_max_tokens": 512,
  "user_temperature": 1.0,
  "interaction_mode": "bedrock",
  "max_turns": 10,
  "turn_delay": 30.0,
  "log_audio": true,
  "log_text": true,
  "log_tools": true,
  "output_directory": "results",
  "auto_evaluate": true,
  "evaluation_criteria": {
    "user_goal": "What the user is trying to accomplish",
    "assistant_objective": "What the agent should do correctly",
    "evaluation_aspects": [
      "Goal Achievement",
      "Tool Usage",
      "Response Quality",
      "Conversation Flow",
      "System Prompt Compliance"
    ]
  },
  "tool_registry_module": "examples.<agent_name>_tools"
}
```

## System Prompt Best Practices

Based on evaluation failures observed in practice, always include these sections:

### 1. Ground the current date

Nova Sonic does not inherently know today's date. Without this, relative dates like "next month" resolve to wrong years.

```
== Today's date ==
Today is 2026-05-29. Use this to resolve relative dates.
Next month means 2026-06-01. Mid-June means 2026-06-15.
```

### 2. Explicit tool-backed truthfulness

Prevent hallucination of addresses, prices, tour slots, or any factual data.

```
== Tool-backed truthfulness ==
Never invent, guess, or fabricate facts. All property details must come from
a successful tool result in this conversation. If you do not have a tool
result for something the caller asks, say you do not have that detail and
offer to connect them with the team.

Never state an address without calling get_property_addresses first.
Never offer tour times without calling get_tour_schedule_time_slots first.
Never quote a price without a get_pricing or get_unit_matrix result.
```

### 3. No redundant tool calls

```
Do not call the same tool twice with identical parameters. If you already
have the result from a previous call, use it directly.
```

### 4. Conversation flow guidance

Prevent formulaic patterns like pushing tours after every response.

```
== Conversation style ==
Follow the caller's lead. Ask one question at a time that moves the
conversation forward based on what they are currently asking about.
Do not repeatedly suggest actions the caller has not asked about.
Only suggest next steps when the caller signals readiness.
```

### 5. Date format in tool descriptions

Add format hints in the `inputSchema` description fields:

```json
"move_in_date": {
  "type": "string",
  "description": "Move-in date in YYYY-MM-DD format, e.g. 2026-06-01"
}
```

## Interaction Modes

| Mode | `interaction_mode` | User input source |
|------|-------------------|-------------------|
| LLM user sim | `"bedrock"` | Claude/Qwen generates user messages dynamically |
| Scripted | `"scripted"` | Fixed messages from `scripted_messages` array |

### Scripted mode

For deterministic, repeatable tests:

```json
{
  "interaction_mode": "scripted",
  "scripted_messages": [
    "Hi, I'm looking for a one-bedroom for next month.",
    "How much is unit four-eleven?",
    "What are the lease term options?"
  ],
  "max_turns": 3
}
```

### LLM user sim mode

For realistic, varied conversations. The user sim prompt should:
- Define a clear persona and goal
- Specify the conversation flow (ask X first, then Y)
- Include "Do not use markdown" and "Keep responses short"
- Avoid asking about things outside the test scope

## User Sim Prompt Tips

```
You are a prospective renter calling a leasing office. You want to find a
one-bedroom for next month and learn about pricing. Be natural and
conversational as if on a phone call. Keep responses short, one to two
sentences. Do not use markdown. Do not ask about tours until your pricing
questions are answered.
```

## Tool Spec Format

Each tool in `sonic_tool_config.tools`:

```json
{
  "toolSpec": {
    "name": "exact_tool_name",
    "description": "Clear description of what this tool does and when to use it",
    "inputSchema": {
      "json": {
        "type": "object",
        "properties": {
          "param_name": {
            "type": "string",
            "description": "What this param is and its format"
          }
        },
        "required": ["param_name"]
      }
    }
  }
}
```

**Important:** The `name` field must exactly match the tool name registered in the tool handler module.

## Evaluation Aspects

Use these built-in metric names (the judge has rubrics for each):

| Metric | What it checks |
|--------|---------------|
| `Goal Achievement` | Did the agent accomplish what the user asked? |
| `Tool Usage` | Correct tools, accurate params, no redundant calls |
| `Response Quality` | Factual accuracy, no hallucination, relevance |
| `Conversation Flow` | Natural flow, no robotic patterns, no repetition |
| `System Prompt Compliance` | Stays in character, follows constraints |

## Dataset-Driven Testing (JSONL)

For batch evaluation with multiple scripted scenarios, create a JSONL file at `datasets/<name>.jsonl`:

```jsonl
{"id": "scenario_1", "turns": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "", "expected_tool_call": "tool_name"}], "rubric": "What the judge should check", "category": "category_name"}
```

## Running

```bash
# Single scenario
python main.py --config configs/<agent>/<scenario>.json

# All scenarios in a directory
python main.py --scenarios-dir configs/<agent> --parallel 2

# Repeat for statistical confidence
python main.py --config configs/<agent>/<scenario>.json --repeat 5 --parallel 3

# Dataset-driven
python main.py --dataset datasets/<name>.jsonl

# View results
streamlit run evaluation/evaluation_dashboard.py
```
