# Nova Sonic Test Harness

An end-to-end test framework for evaluating [Amazon Nova Sonic](https://docs.aws.amazon.com/nova/latest/userguide/speech.html) (speech-to-speech model) via AWS Bedrock's bidirectional streaming API. It simulates multi-turn conversations between a configurable user simulator (Bedrock LLMs or scripted inputs) and Nova Sonic, logs all interactions, and provides LLM-as-judge evaluation.

No microphone or speaker required — uses silent audio streaming or Amazon Polly TTS for realistic audio input.

## Table of Contents

- [Quick Start](#quick-start)
- [Setup](#setup)
- [Running Tests](#running-tests)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Tool System](#tool-system)
- [Evaluation System](#evaluation-system)
- [Results & Output](#results--output)
- [Model Registry](#model-registry)
- [Session Continuation](#session-continuation)
- [Module Reference](#module-reference)
- [Directory Structure](#directory-structure)
- [Examples](#examples)
- [Skills](#skills)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure AWS credentials (choose one)
aws configure sso              # Option A: IAM Identity Center (recommended)
aws configure                  # Option B: Named profile
# Or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in your shell

# 3. (Optional) Set non-secret config like region/model ID
cp .env.example .env

# 4. Run a basic conversation
python main.py --config configs/example_basic.json

# 5. Run with tools
python main.py --config configs/example_with_tools.json

# 6. Batch test all configs in a directory
python main.py --scenarios-dir configs/order_status --parallel 2
```

---

## Setup

### Prerequisites

- **Python 3.12** (managed via [mise](https://mise.jdx.dev/), see `.mise.toml`)
- **AWS credentials** with access to the services below

### Required AWS IAM Permissions

| Service | Permissions | When Needed |
|---|---|---|
| **Amazon Bedrock** | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:InvokeModelWithBidirectionalStream` | Always (Nova Sonic streaming, user simulator, judge) |
| **Amazon Polly** | `polly:SynthesizeSpeech` | Only when `input_mode: "polly"` |
| **Amazon S3** | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:CreateBucket` | Only for audio-text consistency evaluation (`evaluate_audio_text.py`) |
| **Amazon Transcribe** | `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`, `transcribe:DeleteTranscriptionJob` | Only for audio-text consistency evaluation |

Your IAM user/role also needs access to the specific Bedrock models configured in `configs/models.yaml` (Nova Sonic, Claude, etc.). Model access must be enabled in the Bedrock console for your region.

### Install

```bash
pip install -r requirements.txt
```

Key dependencies:

| Package | Purpose |
|---|---|
| `boto3` | AWS SDK for Bedrock Converse API (user sim, judge) |
| `aws_sdk_bedrock_runtime` | Bidirectional streaming with Nova Sonic |
| `rx` (RxPY) | Reactive streams for event-driven audio/text |
| `smithy-aws-core` | Low-level AWS signing for streaming |
| `pyyaml` | Config file loading |
| `python-dotenv` | `.env` file support |

### AWS Credentials

Use one of the standard AWS credential mechanisms — **do not** store access keys in a `.env` file.

**Option 1 — IAM Identity Center (SSO)** (recommended for organizations):

```bash
aws configure sso
# Then run with:
AWS_PROFILE=your-sso-profile python main.py --config configs/example_basic.json
```

**Option 2 — Named profile via AWS CLI:**

```bash
aws configure --profile nova-sonic-eval
# Then run with:
AWS_PROFILE=nova-sonic-eval python main.py --config configs/example_basic.json
```

**Option 3 — Environment variables** (ephemeral, per shell session):

```bash
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...  # if using temporary credentials
export AWS_DEFAULT_REGION=us-east-1
```

For non-secret configuration (model IDs, regions), you can optionally copy `.env.example` to `.env`:

```bash
cp .env.example .env
# Edit .env to set SONIC_MODEL_ID, SONIC_REGION, etc.
```

Environment variables override config file values for `sonic_model_id`, `sonic_region`, and `sonic_endpoint_uri`.

---

## Running Tests

### Single Conversation

```bash
python main.py --config configs/example_basic.json
```

### With Tools Enabled

```bash
python main.py --config configs/example_with_tools.json
```

### Scripted Mode (Pre-Recorded User Messages)

```bash
python main.py --config configs/example_scripted.json
```

### Batch Testing (All Configs in a Directory)

```bash
python main.py --scenarios-dir configs/order_status --parallel 2
```

### Repeat a Config Multiple Times

```bash
python main.py --config configs/claude_haiku_basic.json --repeat 5 --parallel 3
```

### Override Config from CLI

```bash
python main.py --config configs/example_basic.json --max-turns 5 --no-evaluate
```

### Evaluate an Existing Log

```bash
# Evaluate an existing conversation log
python main.py evaluate results/sessions/<session_id>/ \
  --user-goal "Check order status" \
  --assistant-objective "Provide accurate order information" \
  --aspects "Goal Achievement,Tool Usage,Response Quality"

# With custom rubric questions
python main.py evaluate results/sessions/<session_id>/ \
  --aspects "Goal Achievement,Tool Usage,Response Quality" \
  --rubrics '{"Goal Achievement": ["Did the agent find the order?", "Did the agent provide the status?"]}'
```

### Audio-Text Consistency Check (Hallucination Detection)

```bash
python main.py evaluate-audio \
  --log results/sessions/<session_id> \
  --s3-bucket my-transcription-bucket
```

Requires `log_audio: true` in the config. Transcribes each turn's audio via Amazon Transcribe and compares against the text output to detect hallucinations. Audio files are uploaded to S3 keyed by the turn's Nova Sonic session ID.

### Polly TTS Mode (Real Audio Input)

```bash
# Basic Polly TTS conversation
python main.py --config configs/example_polly.json

# Polly TTS with tools
python main.py --config configs/example_with_tools_polly.json

# Enable Polly via CLI overrides on any config
python main.py --config configs/example_basic.json \
  --input-mode polly --polly-voice Matthew --record-conversation --sonic-voice tiffany
```

Instead of sending text to Nova Sonic, Polly TTS synthesizes user simulator text into 16kHz audio and streams it as real speech input. Set `record_conversation: true` to save input/output audio as LPCM files and a merged stereo WAV.

### Dataset-Driven Testing

Run test scenarios from external datasets (JSONL files or HuggingFace datasets). Each entry becomes a separate scripted session with optional ground truth for evaluation.

```bash
# Run a local JSONL dataset
python main.py --dataset datasets/example_customer_support.jsonl

# With parallelism
python main.py --dataset datasets/example_customer_support.jsonl --parallel 4

# With a base config (provides system prompt, tools, voice, etc.)
python main.py --dataset datasets/my_tests.jsonl --base-config configs/example_dataset.json

# HuggingFace dataset
python main.py --dataset hf://ScaleAI/audiomc

# Via config file (set dataset_path in the config)
python main.py --config configs/example_dataset.json
```

**Dataset format (JSONL)** — two styles auto-detected:

Style A — Turns array (standard):
```jsonl
{"id": "order_001", "category": "tool_usage", "turns": [{"role": "user", "content": "Check order ORD-999"}, {"role": "assistant", "content": "Your order is in transit.", "expected_tool_call": "getOrderStatus"}], "rubric": "Must use getOrderStatus tool", "config": {"sonic_system_prompt": "You are a support agent."}}
```

Style B — Wide format (AudioMC-compatible):
```jsonl
{"id": "music_001", "user_turn_1_transcript": "Play Bowie", "assistant_turn_1_transcript": "Here's Let's Dance", "user_turn_2_transcript": "Something mellow?", "assistant_turn_2_transcript": "Here's Space Oddity", "rubric": "Should remember artist across turns"}
```

Each entry can include:
- `id` — unique identifier
- `category` — test category/axis
- `rubric` — evaluation criteria text (injected into judge prompt)
- `config` — per-entry config overrides (e.g. system prompt, tools)
- Expected responses and tool calls serve as ground truth for the LLM judge

### Quick Run via Shell Script

```bash
./run_example.sh claude_haiku_basic
./run_example.sh gpt_oss_tools
./run_example.sh scripted
```

### CLI Arguments

| Argument | Description |
|---|---|
| `--config PATH` | Single configuration file |
| `--scenarios-dir DIR` | Directory of configs (mutually exclusive with `--config`) |
| `--dataset PATH` | JSONL dataset or `hf://org/dataset` (mutually exclusive with above) |
| `--base-config PATH` | Base config for dataset runs (used with `--dataset`) |
| `--pattern GLOB` | Config file pattern (default: `*.json`) |
| `--test-name NAME` | Override test name |
| `--max-turns N` | Override max turns |
| `--mode bedrock\|scripted` | Override interaction mode |
| `--parallel N` | Parallel session count (default: 1) |
| `--repeat N` | Repeat each config N times (default: 1) |
| `--no-evaluate` | Disable auto-evaluation |
| `--log-judge-prompts` | Log judge prompts and raw responses in evaluation output |
| `--sonic-voice ID` | Nova Sonic output voice (e.g. `matthew`, `tiffany`, `amy`) |
| `--input-mode text\|polly` | Audio input mode: `text` (default) or `polly` (TTS) |
| `--polly-voice ID` | Polly voice ID (e.g. `Matthew`, `Joanna`) |
| `--record-conversation` | Record full conversation audio (LPCM + stereo WAV) |
| `--output-dir DIR` | Output directory for results (default: `results`) |
| `--log-events` | Log all streaming events to JSONL (debug mode) |

---

## Configuration

Configurations are JSON or YAML files in `configs/`. All settings are defined in the `TestConfig` dataclass (`config_manager.py`).

### Configuration Reference

```json
{
  "test_name": "order_status_delayed",
  "description": "Test handling of delayed order inquiries",

  "sonic_model_id": "nova-sonic",
  "sonic_region": "us-east-1",
  "sonic_endpoint_uri": null,
  "sonic_system_prompt": "You are a customer service agent...",
  "sonic_inference_config": {
    "max_tokens": 1024,
    "temperature": 0.7,
    "top_p": 0.9
  },
  "sonic_tool_config": {
    "tools": [ { "toolSpec": { "..." } } ],
    "tool_choice": { "auto": {} }
  },

  "sonic_voice_id": "matthew",

  "user_model_id": "claude-haiku",
  "user_system_prompt": "You are an angry customer...",
  "user_max_tokens": 1024,
  "user_temperature": 1.0,

  "input_mode": "text",
  "polly_voice_id": "Matthew",
  "polly_engine": "neural",
  "polly_region": null,
  "record_conversation": false,

  "interaction_mode": "bedrock",
  "scripted_messages": [],
  "max_turns": 10,
  "turn_delay": 1.0,

  "log_audio": false,
  "log_text": true,
  "log_tools": true,
  "output_directory": "results",

  "auto_evaluate": true,
  "evaluation_criteria": {
    "user_goal": "Get answers about delayed order",
    "assistant_objective": "Provide accurate order info and de-escalate",
    "evaluation_aspects": [
      "Goal Achievement",
      "Conversation Flow",
      "Tool Usage Appropriateness",
      "Response Accuracy",
      "User Experience"
    ],
    "rubrics": {
      "Goal Achievement": [
        "Did the agent provide the order status?",
        "Did the agent offer a resolution?"
      ]
    }
  },

  "tool_registry_module": "tools.order_status_tools",

  "enable_session_continuation": true,
  "transition_threshold_seconds": 360.0,
  "audio_buffer_duration_seconds": 3.0
}
```

### Configuration Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `test_name` | string | required | Name for this test scenario |
| `description` | string | `""` | Human-readable description |
| **Nova Sonic** ||||
| `sonic_model_id` | string | `"nova-sonic"` | Model alias or full Bedrock ID |
| `sonic_region` | string | `"us-east-1"` | AWS region for Nova Sonic |
| `sonic_endpoint_uri` | string | `null` | Custom endpoint URI (optional) |
| `sonic_system_prompt` | string | `"You are a helpful assistant."` | System prompt for Nova Sonic |
| `sonic_inference_config` | object | `{max_tokens:1024, temperature:0.7, top_p:0.9}` | Inference parameters |
| `sonic_tool_config` | object | `null` | Tool definitions and tool_choice |
| `sonic_voice_id` | string | `"matthew"` | Nova Sonic output voice (e.g. `matthew`, `tiffany`, `amy`) |
| **User Simulator** ||||
| `user_model_id` | string | `"claude-haiku"` | Model alias for user simulation |
| `user_system_prompt` | string | `null` | System prompt for the simulated user |
| `user_max_tokens` | int | `1024` | Max tokens for user messages |
| `user_temperature` | float | `1.0` | Temperature for user generation |
| **Audio Input** ||||
| `input_mode` | string | `"text"` | `"text"` (interactive text) or `"polly"` (Polly TTS audio) |
| `polly_voice_id` | string | `"Matthew"` | Polly voice ID for TTS synthesis |
| `polly_engine` | string | `"neural"` | Polly engine (`neural` or `standard`) |
| `polly_region` | string | `null` | AWS region for Polly (defaults to `sonic_region`) |
| `record_conversation` | bool | `false` | Record input/output audio (LPCM + stereo WAV) |
| **Interaction** ||||
| `interaction_mode` | string | `"bedrock"` | `"bedrock"` (LLM user) or `"scripted"` (predefined messages) |
| `scripted_messages` | list | `[]` | Messages for scripted mode |
| `max_turns` | int | `10` | Maximum conversation turns |
| `turn_delay` | float | `1.0` | Seconds between turns |
| **Logging** ||||
| `log_audio` | bool | `true` | Record audio to WAV files |
| `log_text` | bool | `true` | Log text transcripts |
| `log_tools` | bool | `true` | Log tool calls and results |
| `output_directory` | string | `"results"` | Base output directory |
| `log_events` | bool | `true` | Log all streaming events to JSONL |
| **Evaluation** ||||
| `auto_evaluate` | bool | `false` | Run LLM judge after conversation |
| `evaluation_criteria` | object | `null` | User goal, assistant objective, aspects, rubrics |
| `log_judge_prompts` | bool | `false` | Record judge prompt and raw response in evaluation output |
| **Tools** ||||
| `tool_registry_module` | string | `null` | Python module path for custom tools |
| **Dataset** ||||
| `dataset_path` | string | `null` | Path to JSONL dataset or `hf://org/dataset` |
| **Session Continuation** ||||
| `enable_session_continuation` | bool | `true` | Auto-reconnect on timeout |
| `transition_threshold_seconds` | float | `360.0` | When to start new session |
| `audio_buffer_duration_seconds` | float | `3.0` | Audio buffer for transitions |

### Included Configs

| Config | Mode | Tools | Turns | User Model |
|---|---|---|---|---|
| `example_basic.json` | bedrock | No | 5 | claude-haiku |
| `example_with_tools.json` | bedrock | Yes (3 travel tools) | 10 | claude-haiku |
| `example_scripted.json` | scripted | Yes | 1 | (scripted) |
| `claude_haiku_basic.json` | bedrock | No | 5 | claude-haiku |
| `gpt_oss_tools.json` | bedrock | Yes | 10 | gpt-oss |
| `qwen_complex.json` | bedrock | No | 10 | qwen |
| `qwen_with_tools.json` | bedrock | Yes | 10 | qwen |
| `example_binary_eval.json` | bedrock | No | 5 | claude-haiku (binary judge) |
| `example_polly.json` | bedrock | No | 5 | claude-haiku (Polly TTS) |
| `example_with_tools_polly.json` | bedrock | Yes (3 travel tools) | 1 | claude-haiku (Polly TTS) |
| `example_dataset.json` | dataset | No | (per entry) | (scripted) |
| `order_status/*.json` | bedrock | Yes (lookupOrder) | 4-5 | claude-haiku |

The `order_status/` directory contains 5 scenario variants: aggressive, delayed, normal, processing, wrong_id — each with different user personas and expected order states.

### Legacy Key Migration

Old `claude_*` keys are auto-migrated to `user_*`:

| Old Key | New Key |
|---|---|
| `claude_model` | `user_model_id` |
| `claude_system_prompt` | `user_system_prompt` |
| `claude_max_tokens` | `user_max_tokens` |
| `claude_temperature` | `user_temperature` |

---

## Architecture

### Core Pipeline Flow

```
                          ┌──────────────────┐
                          │   main.py        │
                          │ LiveInteraction  │
                          │    Session       │
                          └──────┬───────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                   │
              ▼                  ▼                   ▼
   ┌──────────────────┐  ┌─────────────┐  ┌──────────────────┐
   │ BedrockModelClient│  │SonicStream  │  │InteractionLogger │
   │ (user simulator)  │  │  Manager    │  │ (JSON + WAV)     │
   │                   │  │ (RxPY)      │  │                  │
   └──────────────────┘  └──────┬──────┘  └──────────────────┘
                                │
                         ┌──────┴──────┐
                         │ Session     │
                         │ Continuation│
                         │ Manager     │
                         └─────────────┘
```

1. **`main.py`** (`LiveInteractionSession`) orchestrates the turn loop
2. Each turn: user simulator generates text -> sent to Nova Sonic via streaming (as text, or synthesized to speech via Polly TTS) -> Nova Sonic responds with text/audio/tool-use events -> turn logged
3. After all turns, optionally runs LLM judge evaluation
4. Results organized into `results/sessions/` by `ResultsManager`

### Event-Driven Streaming (RxPY)

`sonic_stream_manager.py` uses reactive streams for bidirectional communication with Nova Sonic. The initialization sequence:

1. `sessionStart` (inference config)
2. `promptStart` (audio/text/tool output config, tool definitions)
3. System prompt (`contentStart` -> `textInput` -> `contentEnd`)
4. Audio `contentStart` (USER role, AUDIO type)
5. Silent audio streaming begins (512-frame chunks at 30ms intervals)

Events emitted by Nova Sonic and handled by the callback:

| Event | Description |
|---|---|
| `completion_start` | Fires once per Nova Sonic session; captures `sonic_session_id` |
| `content_start` | New content block begins (tracks `generationStage` and `role`) |
| `text_output` | Text chunk (SPECULATIVE or FINAL stage) |
| `audio_output` | Audio chunk (base64 PCM) |
| `tool_use` | Tool call with name, ID, and parameters |
| `content_end` | Content block ends (may contain `stopReason`) |

All event callbacks include `sonic_session_id` — the Nova Sonic server-side session UUID. This changes when session continuation creates a new connection, allowing each turn's audio to be mapped to the exact Nova Sonic session that produced it.

### Turn Completion Detection

Critical logic in `main.py`:

- **Primary**: Speculative text count == final text count (most reliable). Each text block goes through SPECULATIVE then FINAL stages. When all speculative blocks have finalized, the turn is complete.
- **Safety net**: 60-second timeout for initial response, 60-second stall timeout between FINAL texts.

### Hangup Detection

The user simulator can emit `[HANGUP]` in its message to signal the conversation should end early (e.g., frustrated customer hangs up). The turn loop detects this and stops cleanly.

When `hangup_prompt_enabled` is `true` (the default), the harness automatically appends a `[HANGUP]` instruction to the user simulator's system prompt. This means you do **not** need to include `[HANGUP]` instructions in your config's `user_system_prompt`. If the prompt already contains `[HANGUP]`, the postamble is skipped to avoid duplication. Set `"hangup_prompt_enabled": false` in your config to disable this behavior.

---

## Tool System

### Tool Definitions in Config

Tools are defined in `sonic_tool_config.tools` using the Bedrock toolSpec format:

```json
{
  "toolSpec": {
    "name": "lookupOrder",
    "description": "Look up order status by order ID",
    "inputSchema": {
      "json": {
        "type": "object",
        "properties": {
          "order_id": {
            "type": "string",
            "description": "The order ID to look up"
          }
        },
        "required": ["order_id"]
      }
    }
  }
}
```

### Tool Registry (Custom Implementations)

Register async handlers via `tool_registry.py`:

```python
from tool_registry import ToolRegistry

registry = ToolRegistry()

@registry.tool("lookupOrder")
async def lookup_order(tool_input: dict) -> dict:
    order_id = tool_input.get("order_id")
    # Call your actual API...
    return {"status": "success", "order": {"id": order_id, "state": "shipped"}}
```

To use a registry from a config file, set `tool_registry_module`:

```json
{
  "tool_registry_module": "tools.order_status_tools"
}
```

The module must export a `registry` attribute (a `ToolRegistry` instance). It's loaded via `importlib` at startup.

### Mock Fallback

If no handler is registered for a tool call, `main.py` falls back to a mock response with a 0.5-second simulated delay. This lets you run tool-enabled configs without implementing every tool.

### Tool Call Flow

1. Nova Sonic emits a `toolUse` event with `toolName`, `toolUseId`, and content
2. `SonicStreamManager._handle_tool_request()` calls the registered handler
3. Result is sent back via `contentStart(TOOL)` -> `toolResult` -> `contentEnd`
4. Nova Sonic continues generating based on the tool result

---

## Evaluation System

Binary LLM judge with strict YES/NO verdicts per metric. Active when `auto_evaluate: true` in config.

### Binary Judge (`llm_judge_binary.py`)

Each metric returns a strict YES/NO verdict. Useful for automated CI/CD pass/fail gating and aggregated metric pass rates.

- Four built-in metrics: **Goal Achievement**, **Tool Usage**, **Response Quality**, **Conversation Flow**
- Tool Usage auto-skipped when no tools are configured
- Supports **multiple rubric questions** per metric (each independently YES/NO). A metric passes only if ALL its rubric questions pass
- Custom rubrics via `evaluation_criteria.rubrics` config field
- Extensible: add new metrics to `BUILTIN_METRICS` or pass `custom_metrics` at runtime

```python
from llm_judge_binary import BinaryLLMJudge
from evaluation_types import BinaryEvaluationCriteria

judge = BinaryLLMJudge(judge_model="claude-opus")
criteria = BinaryEvaluationCriteria(
    user_goal="Book a flight to Paris",
    assistant_objective="Provide travel assistance",
    evaluation_aspects=["Goal Achievement", "Tool Usage", "Response Quality", "Conversation Flow"],
    rubrics={
        "Goal Achievement": [
            "Did the agent help find flights to Paris?",
            "Did the agent check the rewards balance?",
        ]
    },
)
result = judge.evaluate_conversation(conversation_log, criteria, config=eval_config)
# result.pass_fail -> True/False (all metrics must pass)
# result.pass_rate -> 0.75 (3 of 4 metrics passed)
# result.metric_verdicts -> per-metric YES/NO with reasoning
```

**Config example:**

```json
{
  "auto_evaluate": true,
  "evaluation_criteria": {
    "user_goal": "Book a flight and check rewards",
    "assistant_objective": "Provide travel assistance",
    "evaluation_aspects": ["Goal Achievement", "Tool Usage", "Response Quality", "Conversation Flow"],
    "rubrics": {
      "Goal Achievement": [
        "Did the agent help find flights to Paris?",
        "Did the agent check the rewards balance?"
      ]
    }
  }
}
```

**Output format:**

```json
{
  "rating_type": "binary",
  "results": {
    "overall_rating": "PASS",
    "pass_fail": true,
    "pass_rate": 1.0,
    "metric_verdicts": {
      "Goal Achievement": {
        "verdict": "PASS",
        "reasoning": "...",
        "rubric_verdicts": [
          {"question": "Did the agent help find flights?", "verdict": "YES", "reasoning": "..."}
        ]
      }
    }
  }
}
```

### Standalone Log Evaluation

```bash
python main.py evaluate results/sessions/<session_id>/ \
  --user-goal "Custom user goal" \
  --assistant-objective "Custom assistant objective" \
  --aspects "Goal Achievement,Tool Usage,Response Quality" \
  --rubrics '{"Goal Achievement": ["Did the agent help?"]}'
```

### Path D: Audio-Text Consistency Evaluation (`evaluate_audio_text.py`)

Detects hallucinations by comparing Nova Sonic's text output against a transcription of its audio output via Amazon Transcribe.

```bash
python main.py evaluate-audio \
  --log results/sessions/<session_id> \
  --s3-bucket my-transcription-bucket \
  --region us-east-1 \
  --judge-model claude-opus
```

**How it works:**

1. For each turn with recorded audio, uploads the WAV file to S3 (keyed by the turn's Nova Sonic `sonic_session_id`, falling back to the log session ID)
2. Runs an Amazon Transcribe job and fetches the transcript
3. An LLM judge compares the text output vs. audio transcription, classifying differences as factual discrepancies, phrasing differences, or filler words
4. Saves `audio_text_consistency.json` in the log directory and cleans up S3 artifacts

**Per-turn verdicts:**

| Verdict | Meaning |
|---|---|
| `CONSISTENT` | Only filler words or no differences |
| `MINOR_DIFFERENCES` | Phrasing differences only (synonyms, reworded sentences) |
| `HALLUCINATION` | Factual discrepancies (different numbers, names, facts) |

**Requirements:**

- `log_audio: true` in the config (so WAV files are recorded per turn)
- An S3 bucket for temporary audio uploads (created automatically if it doesn't exist)
- Amazon Transcribe access in the specified region

**Output:**

```json
{
  "session_id": "order_status_20260225_170309_0b85b6",
  "judge_model": "claude-opus",
  "total_turns": 5,
  "turns_evaluated": 4,
  "turns_skipped": 1,
  "turns_hallucinated": 0,
  "hallucination_rate": 0.0,
  "turn_results": [
    {
      "turn_number": 1,
      "verdict": "CONSISTENT",
      "sonic_response_text": "...",
      "audio_transcript": "...",
      "factual_discrepancies": [],
      "phrasing_differences": [],
      "filler_words": ["um"]
    }
  ]
}
```

---

## Results & Output

### Session Output

All session output goes to `{output_directory}/sessions/{session_id}/` (default: `results/sessions/`):

```
results/sessions/{session_id}/
├── audio/                          # Audio files
│   ├── turn_1.wav                  # Per-turn audio (if log_audio: true)
│   ├── input.lpcm                  # Raw input audio (if record_conversation: true)
│   ├── output.lpcm                 # Raw output audio (if record_conversation: true)
│   └── conversation.wav            # Stereo WAV L=input R=output (if record_conversation: true)
├── chat/
│   └── conversation.txt            # Human-readable transcript
├── config/
│   └── test_config.json            # Test configuration used
├── logs/
│   ├── interaction_log.json        # Complete structured data
│   └── events.jsonl                # Streaming events debug log (if log_events: true)
├── evaluation/
│   └── llm_judge_evaluation.json   # LLM judge evaluation results
├── session_metadata.json
├── README.json                     # Quick reference with statistics
└── README.md
```

### Indexes

- `results/master_index.json` — Array of all sessions with metadata
- `results/sessions_index.csv` — CSV version for spreadsheets

### Batch Results

When running `--scenarios-dir` or `--parallel`, batch summaries are generated:

- `results/batches/{batch_id}/batch_summary.json` — Aggregated stats, pass rates, rating distributions

### Interaction Log Format

```json
{
  "session_id": "order_status_normal_20260225_144223_a1b2c3",
  "test_name": "order_status_normal",
  "start_time": "2026-02-25T14:42:23.261709",
  "end_time": "2026-02-25T14:47:17.065399",
  "configuration": { "...full TestConfig..." },
  "turns": [
    {
      "turn_number": 1,
      "timestamp": "2026-02-25T14:42:25.000000",
      "user_message": "Hi, I'd like to check on my order ORD-2024-33100.",
      "sonic_response": "Of course! Let me look that up for you.",
      "tool_calls": [
        {
          "tool_name": "lookupOrder",
          "tool_use_id": "uuid",
          "tool_input": { "order_id": "ORD-2024-33100" },
          "tool_result": { "status": "shipped", "tracking": "..." },
          "timestamp": "2026-02-25T14:42:30.000000"
        }
      ],
      "audio_recorded": true,
      "audio_file": "turn_1.wav",
      "sonic_session_id": "04934a4f-c91b-4756-bd11-8c0b41890489",
      "metadata": {}
    }
  ],
  "total_turns": 4,
  "total_tool_calls": 1,
  "errors": [],
  "session_events": [],
  "summary": {
    "total_turns": 4,
    "total_tool_calls": 1,
    "tool_usage_rate": "25.00%",
    "avg_user_message_length": 236.5,
    "avg_sonic_response_length": 286.0,
    "session_continuations": 0
  }
}
```

### Evaluation Output Format

```json
{
  "evaluated_at": "2026-02-25T14:50:00.000000",
  "judge_model": "global.anthropic.claude-opus-4-5-20251101-v1:0",
  "rating_type": "binary",
  "criteria": {
    "user_goal": "Check order status and get delivery estimate",
    "assistant_objective": "Provide accurate order information",
    "evaluation_aspects": ["Goal Achievement", "Tool Usage", "Response Quality"]
  },
  "results": {
    "overall_rating": "PASS",
    "pass_fail": true,
    "pass_rate": 1.0,
    "metric_verdicts": {
      "Goal Achievement": {
        "verdict": "PASS",
        "reasoning": "The assistant correctly looked up the order and provided status"
      },
      "Tool Usage": {
        "verdict": "PASS",
        "reasoning": "Correctly used lookupOrder tool with proper parameters"
      }
    }
  },
  "conversation_summary": {
    "total_turns": 4,
    "total_tool_calls": 1,
    "session_id": "order_status_normal_20260225_144223_a1b2c3"
  },
  "judge_log": {
    "prompt": "You are an expert evaluator... (full judge prompt)",
    "raw_response": "{\"overall_rating\": \"PASS\", ...}"
  }
}
```

The `judge_log` field is only present when `log_judge_prompts: true` is set in config or `--log-judge-prompts` is passed on the CLI.

---

## Model Registry

`configs/models.yaml` maps short aliases to full Bedrock model IDs:

```yaml
user_models:
  claude-haiku:
    model_id: "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    region: "us-west-2"
  claude-sonnet:
    model_id: "us.anthropic.claude-sonnet-4-20250514-v1:0"
    region: "us-west-2"
  gpt-oss:
    model_id: "openai.gpt-oss-20b-1:0"
    region: "us-west-2"
  qwen:
    model_id: "qwen.qwen3-32b-v1:0"
    region: "us-west-2"

sonic_models:
  nova-sonic:
    model_id: "amazon.nova-2-sonic-v1:0"
    region: "us-east-1"

judge_models:
  claude-opus:
    model_id: "global.anthropic.claude-opus-4-5-20251101-v1:0"
    region: "us-east-1"
  claude-sonnet:
    model_id: "us.anthropic.claude-sonnet-4-20250514-v1:0"
    region: "us-east-1"
```

Three categories:

| Category | Used By | API |
|---|---|---|
| `user_models` | `BedrockModelClient` (user simulator) | Bedrock Converse |
| `sonic_models` | `SonicStreamManager` (Nova Sonic) | Bidirectional Streaming |
| `judge_models` | `BinaryLLMJudge` (evaluation) | Bedrock Converse |

To add a new model, edit `configs/models.yaml` — no code changes needed. Unmatched aliases fall back to being treated as literal Bedrock model IDs.

---

## Session Continuation

Nova Sonic has an approximately 8-minute connection timeout. `SessionContinuationManager` (`session_continuation.py`) wraps `SonicStreamManager` to transparently handle this:

1. Monitors connection age against `transition_threshold_seconds` (default: 360s / 6 min)
2. When approaching timeout, creates a new `SonicStreamManager` session
3. Replays conversation history into the new session using `ConversationHistory` (`conversation_history.py`)
4. History replay has budget limits: 1KB per message, 40KB total
5. The turn loop in `main.py` is unaware of session transitions

Enable/disable via config:

```json
{
  "enable_session_continuation": true,
  "transition_threshold_seconds": 360.0,
  "audio_buffer_duration_seconds": 3.0
}
```

Session events (transitions, recoveries) are logged in `interaction_log.json` under `session_events`.

---

## Module Reference

### Core Pipeline

| Module | Description |
|---|---|
| `main.py` | `LiveInteractionSession` — orchestrates the turn loop, handles events and tool calls |
| `sonic_stream_manager.py` | `SonicStreamManager` — RxPY bidirectional streaming with Nova Sonic |
| `session_continuation.py` | `SessionContinuationManager` — transparent session restart on timeout |
| `conversation_history.py` | `ConversationHistory` — tracks messages for replay (1KB/msg, 40KB budget) |
| `bedrock_model_client.py` | `BedrockModelClient` (LLM user simulator) + `ScriptedUser` (predefined messages) |
| `config_manager.py` | `ConfigManager` + `TestConfig` / `InferenceConfig` / `ToolConfig` dataclasses |
| `interaction_logger.py` | `InteractionLogger` — records turns, audio, tool calls, session events |
| `tool_registry.py` | `ToolRegistry` — decorator-based tool registration and async execution |
| `model_registry.py` | `ModelRegistry` — resolves aliases via `configs/models.yaml` |
| `results_manager.py` | `ResultsManager` — organizes output, generates indexes and summaries |
| `multi_session_runner.py` | `MultiSessionRunner` — batch mode with parallel execution and dataset runs |
| `dataset_loader.py` | `DatasetLoader` + `DatasetEntry` — loads JSONL and HuggingFace datasets |
| `polly_tts_client.py` | `PollyTTSClient` — Amazon Polly TTS wrapper (16kHz 16-bit mono PCM) |
| `conversation_audio_recorder.py` | `ConversationAudioRecorder` — records input/output LPCM + stereo WAV |

### Evaluation

| Module | Description |
|---|---|
| `evaluation_types.py` | `BinaryEvaluationCriteria` + `BinaryEvaluationResult` + `AudioTextConsistencyReport` dataclasses |
| `llm_judge_binary.py` | `BinaryLLMJudge` — binary (YES/NO) evaluation with per-metric rubrics |
| `evaluate_log.py` | Standalone script to evaluate existing logs |
| `evaluate_audio_text.py` | `AudioTextConsistencyJudge` — audio-vs-text hallucination detection |
| `audio_transcription_client.py` | `TranscriptionClient` — Amazon Transcribe wrapper (S3 upload, polling, cleanup) |

### Module Dependency Graph

```
main.py
├── sonic_stream_manager.py (RxPY, aws_sdk_bedrock_runtime)
├── session_continuation.py -> conversation_history.py
├── bedrock_model_client.py -> model_registry.py -> configs/models.yaml
├── config_manager.py
├── interaction_logger.py
├── tool_registry.py
├── results_manager.py
├── polly_tts_client.py (Amazon Polly, optional)
├── conversation_audio_recorder.py (LPCM + WAV recording, optional)
└── llm_judge_binary.py -> model_registry.py

main.py -> multi_session_runner.py (for batch/parallel runs)
main.py -> dataset_loader.py (for --dataset runs)
multi_session_runner.py -> main.py (LiveInteractionSession), config_manager,
                           results_manager, dataset_loader

evaluate_audio_text.py -> audio_transcription_client.py (Amazon Transcribe via S3)
                       -> model_registry.py (judge model resolution)
```

---

## Directory Structure

```
Nova2SonicTestHarness/
├── main.py                          # Entry point, turn loop orchestration
│
├── core/                            # Core pipeline modules
│   ├── sonic_stream_manager.py      # Bidirectional streaming (RxPY)
│   ├── session_continuation.py      # Auto-reconnect on timeout
│   ├── conversation_history.py      # Message replay for new sessions
│   └── config_manager.py            # Config loading and dataclasses
│
├── clients/                         # External service wrappers
│   ├── bedrock_model_client.py      # User simulator (Bedrock + scripted)
│   ├── polly_tts_client.py          # Amazon Polly TTS wrapper
│   └── audio_transcription_client.py # Amazon Transcribe wrapper
│
├── evaluation/                      # Evaluation and judging
│   ├── llm_judge_binary.py          # LLM-as-judge (binary YES/NO)
│   ├── evaluation_types.py          # Shared eval dataclasses
│   ├── evaluate_log.py              # Standalone log evaluator
│   ├── evaluate_audio_text.py       # Audio-text consistency (hallucination detection)
│   └── evaluation_dashboard.py      # Streamlit dashboard
│
├── logging_/                        # Logging and results management
│   ├── interaction_logger.py        # JSON + WAV logging
│   ├── event_logger.py              # JSONL event debug logging
│   ├── results_manager.py           # Output organization and indexes
│   └── conversation_audio_recorder.py # Input/output LPCM + stereo WAV
│
├── tools/                           # Tool registry and handlers
│   ├── tool_registry.py             # Decorator-based tool registry
│   └── order_status_tools.py        # Order status tool handlers
│
├── runners/                         # Execution orchestration
│   ├── multi_session_runner.py      # Batch/parallel execution
│   └── dataset_loader.py            # JSONL/HuggingFace dataset loading
│
├── utils/                           # Shared utilities
│   ├── model_registry.py            # Alias -> Bedrock model ID
│   └── generate_config.py           # LLM-powered config generator
│
├── configs/                         # Test configurations
│   ├── models.yaml                  # Model alias registry
│   ├── example_basic.json           # Basic conversation
│   ├── example_with_tools.json      # Tools enabled
│   ├── example_polly.json           # Polly TTS audio input
│   ├── example_scripted.json        # Scripted messages
│   ├── example_binary_eval.json     # Binary evaluation with rubrics
│   └── order_status/                # Order status scenario variants
│
├── scenarios/                       # Test scenario configs
│   └── healthcare/                  # Healthcare insurance chatbot scenarios
│
├── datasets/                        # Test datasets (JSONL)
│
├── examples/                        # Example implementations
│   ├── example_with_tool_registry.py
│   ├── healthcare_tools.py
│   └── order_status_tools.py
│
└── results/                         # Organized results
    ├── master_index.json
    ├── sessions_index.csv
    ├── sessions/{session_id}/       # Per-session organized output
    └── batches/{batch_id}/          # Batch summaries
```

---

## Examples

### Basic Conversation (No Tools)

```bash
python main.py --config configs/example_basic.json
```

Claude Haiku simulates a user having a 5-turn conversation with Nova Sonic.

### Tool-Enabled Conversation

```bash
python main.py --config configs/example_with_tools.json
```

Three travel tools (getBookingDetails, searchDestinations, getRewardsBalance) with custom implementations from `examples/example_with_tool_registry.py`.

### Scripted Mode

```bash
python main.py --config configs/example_scripted.json
```

Predefined user messages for repeatable testing. No LLM-generated user input.

### Order Status Scenarios

```bash
python main.py --scenarios-dir configs/order_status --parallel 2
```

Five customer service scenarios with different user personas (aggressive, patient, confused) and order states (delayed, processing, wrong ID). Each uses the `lookupOrder` tool.

### Custom Tool Implementation

```python
# my_tools.py
from tool_registry import ToolRegistry

registry = ToolRegistry()

@registry.tool("getWeather")
async def get_weather(tool_input: dict) -> dict:
    city = tool_input.get("city", "unknown")
    return {"temperature": 72, "condition": "sunny", "city": city}
```

Reference it from your config:

```json
{
  "tool_registry_module": "my_tools",
  "sonic_tool_config": {
    "tools": [{
      "toolSpec": {
        "name": "getWeather",
        "description": "Get current weather for a city",
        "inputSchema": {
          "json": {
            "type": "object",
            "properties": {
              "city": { "type": "string", "description": "City name" }
            },
            "required": ["city"]
          }
        }
      }
    }]
  }
}
```

---

## Audio Parameters

| Parameter | Value |
|---|---|
| Input sample rate | 16 kHz |
| Output sample rate (text mode) | 24 kHz (default, set via `audioOutputConfiguration.sampleRateHertz` in `start_prompt()`) |
| Output sample rate (Polly mode) | 16 kHz (matches input for recording alignment) |
| Polly TTS sample rate | 16 kHz |
| Channels | Mono |
| Bit depth | 16-bit PCM |
| Text chunking | 1,000 chars per `textInput` event |
| Audio chunking | 512-frame chunks (~32ms at 16kHz) |
| Silent audio interval | 30ms between chunks |

No microphone or speaker is needed. In text mode, the harness streams silent audio to satisfy Nova Sonic's requirement for an active audio channel while sending text input. In Polly mode, user simulator text is synthesized to speech and streamed as real audio input.

> **Note:** The output sample rate is controlled by `audioOutputConfiguration.sampleRateHertz` in `sonic_stream_manager.py:start_prompt()`. Nova Sonic respects this setting. When Polly mode is enabled, both input and output use 16kHz so conversation recordings stay aligned.

---

## Skills

The `skills/` directory contains step-by-step guides for common tasks. Each skill encodes project-specific rules, validation steps, and reference material so AI coding agents (or developers) can complete complex workflows without starting from scratch.

### eval-runner — Evaluate a Voice Agent

Entry point: `skills/eval-runner/skill.md`

Given a voice agent's system prompt and tool definitions, this skill walks you through the full evaluation lifecycle:

1. **Create a tool handler module** — adapt the agent's tools to the `ToolRegistry` pattern with realistic mock data
2. **Write evaluation configs** — define test scenarios covering happy paths, edge cases, and error handling
3. **Run evaluations** — single scenarios, batch runs, or dataset-driven tests
4. **Analyze and improve** — interpret LLM judge verdicts, identify failure root causes, and iterate on the prompt

```bash
# Example: run all scenarios for an agent
python main.py --scenarios-dir configs/<agent> --parallel 2

# View results in the dashboard
streamlit run evaluation/evaluation_dashboard.py
```

See `skills/README.md` for the full list of available skills and usage examples.

---

## Troubleshooting

### "Failed to initialize stream"

- Check AWS credentials in `.env` are valid
- Verify the model ID exists in your region (`configs/models.yaml`)
- Ensure `aws_sdk_bedrock_runtime` is the correct version (`>=0.1.0,<0.2.0`)

### "Response timeout - no response from Nova Sonic"

Nova Sonic didn't produce any output within 60 seconds. Possible causes:
- Invalid system prompt or tool configuration
- Region mismatch (Nova Sonic is only available in certain regions)
- Network connectivity issues

### Tool calls return mock responses

If you see "Using mock implementation for {tool_name}", register a real handler. Set `tool_registry_module` in your config or pass a `ToolRegistry` to `LiveInteractionSession`.

### Session continuation events in logs

These are normal. Nova Sonic connections time out after ~8 minutes. The `SessionContinuationManager` automatically reconnects and replays history. Check `session_events` in the interaction log for details.

### Evaluation fails with "No JSON found in response"

The judge model didn't return valid JSON. This occasionally happens with weaker models. Use `claude-opus` for evaluation, or re-run with `python main.py evaluate`.

### "Module has no 'registry' attribute"

Your `tool_registry_module` Python file must export a variable named `registry` that is a `ToolRegistry` instance. See `examples/example_with_tool_registry.py`.

### Polly TTS "AccessDeniedException"

Your IAM user/role needs the `polly:SynthesizeSpeech` permission. Add an IAM policy granting Polly access. If Polly fails at runtime, the harness falls back to sending text input.

### Low-quality user messages

Adjust the `user_system_prompt` to be more specific about the persona and behavior. Increase `user_temperature` for more variety, or decrease it for more focused responses. Consider using scripted mode for reproducible tests.
