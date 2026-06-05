# Eval Runner Skill

Evaluate any Nova Sonic voice agent by simulating multi-turn conversations, running tool calls against mock backends, and scoring the results with an LLM judge.

## What This Skill Does

Given a voice agent's system prompt and tool definitions (or an existing agent codebase), this skill walks you through:

1. **Creating a tool handler module** — Adapt the agent's tools to the eval harness `ToolRegistry` pattern with realistic mock data
2. **Writing evaluation configs** — Define test scenarios with system prompts, tool specs, simulated user behavior, and evaluation criteria
3. **Running evaluations** — Execute single scenarios, batch runs, or dataset-driven tests
4. **Analyzing results** — Interpret LLM judge verdicts, identify failure root causes, and make targeted improvements

## How to Launch This Skill

Ask Kiro to evaluate your agent by providing either a reference to an existing app or a system prompt and tools inline. Examples:

### Option A: Point to an existing agent application

```
I have a customer support agent at agents/support/. Set up evaluation for it
following the eval-runner skill.
```

```
Use the eval-runner skill to create evaluation configs for my banking voice
agent. The code is in src/banking_agent/ — it has 12 tools and a system prompt
in agent.py.
```

### Option B: Provide a system prompt and tools directly

```
Use the eval-runner skill to evaluate this agent:

System prompt: "You are a helpful travel booking assistant. You help users
search flights, check prices, and book tickets. Always confirm before booking."

Tools:
- search_flights(origin, destination, date) → returns available flights
- get_price(flight_id) → returns pricing details  
- book_flight(flight_id, passenger_name, confirmed) → books the ticket
```

### Option C: Evaluate a text-based agent (non-voice)

The harness works for any Nova Sonic agent, including text-focused scenarios:

```
Use the eval-runner skill to evaluate this text agent:

System prompt: "You are a technical support agent for a SaaS product. Help
users troubleshoot issues using the knowledge base. Escalate to human support
if you cannot resolve within 3 turns."

Tools:
- search_kb(query) → searches knowledge base articles
- get_article(article_id) → returns full article content
- create_ticket(user_id, summary, priority) → escalates to human support
```

## Quick Start (Manual)

```bash
# 1. Create your tool module at examples/<agent>_tools.py
#    (see references/tool_module_guide.md)

# 2. Create a config at configs/<agent>/<scenario>.json
#    (see references/config_guide.md)

# 3. Run the evaluation
python main.py --config configs/<agent>/<scenario>.json

# 4. View results
streamlit run evaluation/evaluation_dashboard.py

# 5. Analyze failures and improve
#    (see references/analysis_guide.md)
```

## Files in This Skill

| File | Purpose |
|------|---------|
| `skill.md` | Entry point with process overview and file references |
| `references/tool_module_guide.md` | How to create a `ToolRegistry` tool handler module |
| `references/config_guide.md` | How to write evaluation configs and system prompts |
| `references/analysis_guide.md` | How to read results, diagnose failures, and improve the agent |

## Key Lessons Encoded

This skill captures patterns learned from real evaluation runs:

- **Always ground today's date** in the system prompt — Nova Sonic doesn't know the current date, causing wrong year/month in tool parameters
- **Add missing tools** to the config — if the agent might need to look up account details or order history, the tool must be available or the agent will hallucinate
- **Enforce tool-backed truthfulness** — explicit rules like "never state X without calling Y first"
- **Prevent redundant tool calls** — add "do not call the same tool twice with identical parameters"
- **Control conversation flow** — "follow the caller's lead" prevents repetitive suggestions like pushing upsells after every response
- **Add format hints to tool descriptions** — `"description": "Date in YYYY-MM-DD format, e.g. 2026-06-01"` reduces parameter errors

## Example: Customer Service Agent Evaluation

A sample workflow demonstrates the full process for a customer service voice agent (Alex) with 5 tools that handles order inquiries, returns, and account questions:

- Tool module: `examples/order_status_tools.py` (mock order database)
- Configs: `configs/order_status/` (5 scenarios: normal lookup, delayed order, processing order, aggressive customer, wrong order ID)
- Dataset: `datasets/example_customer_support.jsonl` (scripted multi-turn scenarios)

### Sample System Prompt

```
You are Alex, a friendly customer service agent for ShopFast, an online retailer.
You help customers check order status, process returns, and answer account questions.

== Today's date ==
Today is 2026-05-30.

== Rules ==
- Always verify the customer's order ID before looking up details
- Never state shipping dates, prices, or addresses without a fresh tool result
- If the customer is upset, acknowledge their frustration before proceeding
- Do not offer refunds unless the customer explicitly asks or the order qualifies
- Do not call the same tool twice with identical parameters
```

### Sample Tools

| Tool | Purpose |
|------|---------|
| `lookup_order` | Get order status, tracking, and delivery estimate by order ID |
| `get_return_policy` | Check return eligibility and policy for a given order |
| `initiate_return` | Start a return process for an eligible order |
| `get_account_details` | Look up customer account info (name, email, address) |
| `escalate_to_supervisor` | Transfer the call when the agent cannot resolve the issue |

### Sample Scenarios

| Config | What It Tests |
|--------|---------------|
| `order_status_normal.json` | Happy path — customer asks about a shipped order |
| `order_status_delayed.json` | Order is delayed, customer wants an update |
| `order_status_processing.json` | Order still processing, customer is impatient |
| `order_status_aggressive.json` | Angry customer demanding a refund |
| `order_status_wrong_id.json` | Customer provides an invalid order ID |

## Running Commands

After creating configs, run all scenarios as a batch:

```bash
python main.py --scenarios-dir configs/order_status --parallel 2
```

Other useful commands:

```bash
# Single scenario
python main.py --config configs/order_status/order_status_normal.json

# Repeat for confidence
python main.py --config configs/order_status/order_status_normal.json --repeat 5 --parallel 3

# Dataset-driven
python main.py --dataset datasets/example_customer_support.jsonl

# Evaluate existing session
python main.py evaluate results/sessions/<session_id>/

# View results in the dashboard
streamlit run evaluation/evaluation_dashboard.py
```

## Iterating: Improving the Agent from Results

After running evaluations, use the failures to improve the agent's system prompt and tool definitions. Then re-run to verify the fix.

### Example 1: Fixing wrong dates in tool calls

**Evaluation output shows:**
```
Tool Usage: FAIL
Reason: Agent called lookup_order with date "2024-03-15" but the
conversation is set in 2026. Incorrect parameter value.
```

**Fix — add date grounding to the system prompt:**
```diff
 You are Alex, a friendly customer service agent for ShopFast.
+
+== Today's date ==
+Today is 2026-05-30. Use this when resolving relative dates like
+"last week" or "two days ago".
```

**Fix — add format hint to the tool description:**
```diff
 {
   "name": "lookup_order",
   "description": "Look up order status and delivery details",
   "inputSchema": {
     "properties": {
-      "order_date": { "type": "string", "description": "Date the order was placed" }
+      "order_date": { "type": "string", "description": "Date in YYYY-MM-DD format, e.g. 2026-05-28" }
     }
   }
 }
```

### Example 2: Agent hallucinating facts instead of calling a tool

**Evaluation output shows:**
```
Response Quality: FAIL
Reason: Agent stated the delivery date as "June 3rd" but no tool was
called to retrieve shipping information. This is fabricated.
```

**Fix — add truthfulness rule to the system prompt:**
```diff
+== Truthfulness ==
+Never state delivery dates, tracking numbers, or refund amounts without
+a fresh tool result in this turn. If you don't have the data, say:
+"Let me check that for you."
```

**Fix — add the missing tool to the config's `sonic_tool_config.tools`** if it wasn't available:
```json
{
  "toolSpec": {
    "name": "get_shipping_details",
    "description": "Get tracking number, carrier, and estimated delivery date",
    "inputSchema": {
      "json": {
        "type": "object",
        "properties": {
          "order_id": { "type": "string", "description": "Order ID, e.g. ORD-2026-12345" }
        },
        "required": ["order_id"]
      }
    }
  }
}
```

### Example 3: Repetitive suggestions hurting conversation flow

**Evaluation output shows:**
```
Conversation Flow: FAIL
Reason: Agent asked "Is there anything else I can help you with?" four
times in a 5-turn conversation, even mid-topic.
```

**Fix — update conversation flow guidance in the system prompt:**
```diff
-Always ask if there's anything else you can help with.
+Only ask "Is there anything else?" after fully resolving the customer's
+current issue. Do not ask mid-topic or repeat it if already asked.
```

### Example 4: Redundant tool calls

**Evaluation output shows:**
```
Tool Usage: FAIL
Reason: Agent called lookup_order twice with identical parameters
{"order_id": "ORD-2026-33100"} in the same turn.
```

**Fix — add deduplication rule to the system prompt:**
```diff
+== Tool usage rules ==
+Do not call the same tool twice with identical parameters. If you already
+have the result from an earlier call, use it directly.
```

### Iteration Workflow

```bash
# 1. Run the scenario
python main.py --config configs/order_status/order_status_delayed.json

# 2. Review failures (inline output or dashboard)
streamlit run evaluation/evaluation_dashboard.py

# 3. Edit the system prompt or tool definitions in the config

# 4. Re-run the same scenario to verify the fix
python main.py --config configs/order_status/order_status_delayed.json

# 5. Run the full batch to check for regressions
python main.py --scenarios-dir configs/order_status --parallel 2

# 6. Repeat for statistical confidence
python main.py --config configs/order_status/order_status_delayed.json --repeat 5 --parallel 3
```

Once the eval pass rate is stable, port the prompt and tool description changes back to the live agent.
