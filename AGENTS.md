# AGENTS.md

This file provides guidance to AI coding agents working with this repository.

## Project Overview

Nova Sonic Test Harness — an end-to-end test framework for evaluating **Amazon Nova Sonic** (speech-to-speech model) via AWS Bedrock's bidirectional streaming API. It simulates multi-turn conversations between a configurable user simulator (Bedrock LLMs or scripted inputs) and Nova Sonic, logs all interactions, and provides LLM-as-judge evaluation.

## Setup

- **Python 3.12** (managed via mise, see `.mise.toml`)
- **Install dependencies:** `pip install -r requirements.txt`
- **AWS credentials:** Copy `.env.example` to `.env` and fill in credentials
- No microphone/speaker needed — uses silent audio streaming

### Required AWS IAM Permissions

| Service | Permissions | When Needed |
|---|---|---|
| **Amazon Bedrock** | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:InvokeModelWithBidirectionalStream` | Always (Nova Sonic streaming, user simulator, judge) |
| **Amazon Polly** | `polly:SynthesizeSpeech` | Only when `input_mode: "polly"` |
| **Amazon S3** | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:CreateBucket` | Only for audio-text consistency evaluation |
| **Amazon Transcribe** | `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`, `transcribe:DeleteTranscriptionJob` | Only for audio-text consistency evaluation |

### Key Dependencies

| Package | Purpose |
|---|---|
| `boto3` | AWS SDK for Bedrock Converse API (user sim, judge) |
| `aws_sdk_bedrock_runtime` | Bidirectional streaming with Nova Sonic |
| `rx` (RxPY) | Reactive streams for event-driven audio/text |
| `smithy-aws-core` | Low-level AWS signing for streaming |
| `pyyaml` | Config file loading |
| `python-dotenv` | `.env` file support |
| `pandas` | Results management and CSV export |
| `streamlit` | Evaluation dashboard (optional, for `evaluation/evaluation_dashboard.py`) |
| `plotly` | Dashboard charts (optional, used by dashboard) |

## Directory Structure

```
project_root/
├── main.py                              # Entry point — turn loop orchestration
│
├── core/                                # Core pipeline modules
│   ├── sonic_stream_manager.py          # Bidirectional streaming with Nova Sonic (RxPY)
│   ├── session_continuation.py          # Auto-reconnect on 8-min timeout
│   ├── conversation_history.py          # Message replay for new sessions
│   └── config_manager.py               # Config loading, TestConfig dataclass
│
├── clients/                             # External service wrappers
│   ├── bedrock_model_client.py          # User simulator (Bedrock models + scripted)
│   ├── polly_tts_client.py              # Amazon Polly TTS synthesis
│   └── audio_transcription_client.py    # Amazon Transcribe wrapper
│
├── evaluation/                          # Evaluation and judging
│   ├── llm_judge_binary.py              # Binary YES/NO LLM judge
│   ├── evaluation_types.py              # Shared eval dataclasses
│   ├── evaluate_log.py                  # Evaluate existing conversation logs (also via main.py evaluate)
│   ├── evaluate_audio_text.py           # Audio-text consistency / hallucination detection (also via main.py evaluate-audio)
│   └── evaluation_dashboard.py          # Streamlit dashboard for visualizing results
│
├── logging_/                            # Logging and results (trailing _ avoids stdlib collision)
│   ├── interaction_logger.py            # Per-turn JSON + WAV logging
│   ├── event_logger.py                  # JSONL streaming event debug log
│   ├── results_manager.py              # Session directory organization, indexes, batch summaries
│   └── conversation_audio_recorder.py   # Input/output LPCM + stereo WAV recording
│
├── tools/                               # Tool registry and handler modules
│   ├── tool_registry.py                 # Decorator-based tool registration
│   └── order_status_tools.py            # Example: order status tool handlers
│
├── runners/                             # Execution orchestration
│   ├── multi_session_runner.py          # Batch/parallel session execution
│   └── dataset_loader.py               # JSONL and HuggingFace dataset loading
│
├── utils/                               # Shared utilities
│   ├── model_registry.py               # Model alias → Bedrock model ID resolution
│   └── generate_config.py              # LLM-powered config generator CLI
│
├── configs/                             # Test configurations
│   ├── models.yaml                      # Model alias registry (user, sonic, judge)
│   ├── order_status/                    # Order status scenario variants (5 configs)
│   └── *.json                           # Individual test configs
│
├── scenarios/                           # Complex test scenario configs
│   ├── healthcare/                      # 12 healthcare insurance chatbot scenarios
│   └── banking/                         # 8 retail banking chatbot scenarios
│
├── datasets/                            # Test datasets (JSONL)
│   ├── example_customer_support.jsonl   # Sample customer support scenarios
│   ├── healthcare_eval.jsonl            # Healthcare evaluation dataset
│   └── audiomc_sample_10.jsonl          # AudioMC wide-format sample
│
├── examples/                            # Example tool implementations
│   ├── example_with_tool_registry.py    # Tool registration patterns
│   ├── healthcare_tools.py              # Healthcare tool handlers (mock)
│   ├── banking_tools.py                 # Banking tool handlers (mock)
│   └── order_status_tools.py            # Order status tool handlers (mock)
│
└── results/                             # Generated output (not checked in)
    ├── master_index.json                # Index of all sessions
    ├── sessions_index.csv               # Spreadsheet-friendly index
    ├── sessions/{session_id}/           # Per-session structured output
    │   ├── audio/                       # Per-turn WAV + conversation recording
    │   ├── chat/conversation.txt        # Human-readable transcript
    │   ├── logs/interaction_log.json    # Complete structured log
    │   ├── logs/events.jsonl            # Streaming events debug log
    │   ├── config/test_config.json      # Config used for this session
    │   ├── evaluation/                  # LLM judge results
    │   ├── README.json                  # Quick reference with stats
    │   └── session_metadata.json
    └── batches/{batch_id}/              # Batch run summaries
        └── batch_summary.json           # Aggregated stats, per-session results
```

## Running

```bash
# Single conversation
python main.py --config configs/claude_haiku_basic.json

# With tools enabled
python main.py --config configs/example_with_tools.json

# Scripted mode (pre-recorded user messages)
python main.py --config configs/example_scripted.json

# Batch testing (all configs in a directory)
python main.py --scenarios-dir configs --parallel 2

# Repeat a single config N times
python main.py --config configs/claude_haiku_basic.json --repeat 5 --parallel 3

# Override config settings from CLI
python main.py --config configs/claude_haiku_basic.json --max-turns 5 --no-evaluate

# Evaluate an existing conversation log
python main.py evaluate results/sessions/<session_id>/

# Evaluate with custom criteria
python main.py evaluate results/sessions/<session_id>/ --user-goal "Check order status" --aspects "Goal Achievement,Tool Usage"

# Audio-text consistency check (hallucination detection)
python main.py evaluate-audio --log results/sessions/<session_id> --s3-bucket my-bucket

# Polly TTS mode (audio input instead of text)
python main.py --config configs/example_polly.json

# CLI overrides for Polly/voice
python main.py --config configs/example_basic.json --input-mode polly --polly-voice Matthew --record-conversation --sonic-voice tiffany

# Dataset-driven testing (JSONL)
python main.py --dataset datasets/example_customer_support.jsonl

# Dataset with base config and parallelism
python main.py --dataset datasets/my_tests.jsonl --base-config configs/example_dataset.json --parallel 4

# HuggingFace dataset
python main.py --dataset hf://ScaleAI/audiomc

# Config with dataset_path (auto-routes to dataset runner)
python main.py --config configs/example_dataset.json

# Generate a config from natural language
python -m utils.generate_config "customer calls to dispute a credit card charge" --tools
python -m utils.generate_config "tech support for wifi issues" --output configs/wifi_support.json

# Evaluation dashboard (Streamlit)
streamlit run evaluation/evaluation_dashboard.py

# Debug mode with event logging
python main.py --config configs/claude_haiku_basic.json --log-events
```

## Architecture

### Core Pipeline Flow

1. `main.py` (`LiveInteractionSession`) orchestrates the turn loop
2. Each turn: user simulator generates text → sent to Nova Sonic via streaming (as text, or synthesized to speech via Polly TTS) → Nova Sonic responds with text/audio/tool-use events → turn logged
3. After all turns, optionally runs LLM judge evaluation
4. For batch runs, `runners/multi_session_runner.py` orchestrates parallel `LiveInteractionSession` instances and aggregates results via `logging_/results_manager.py`

### Key Architectural Patterns

**Event-driven streaming (RxPY):** `core/sonic_stream_manager.py` uses reactive streams for bidirectional communication with Nova Sonic. Events emitted: `completion_start` (once per session, captures `sonic_session_id`), `content_start`, `text_output`, `audio_output`, `tool_use`, `content_end`. All event callbacks include `sonic_session_id` for per-turn session tracking.

**Turn completion detection** (critical logic in `main.py`):
- Primary: speculative text count == final text count (most reliable)
- Secondary: `END_TURN` signal from `content_end` event
- Safety net: timeout

Nova Sonic generates text in two stages via `contentStart` events with `additionalModelFields.generationStage`:
- **SPECULATIVE** — initial text generation (may change)
- **FINAL** — finalized, confirmed text

Only FINAL text from ASSISTANT role is accumulated into the response buffer.

**Session continuation** (`core/session_continuation.py`): Nova Sonic has an ~8-minute connection timeout. `SessionContinuationManager` wraps `SonicStreamManager` to automatically create new sessions, replay conversation history (50KB/msg, 200KB total budget), and remain transparent to the turn loop.

**Model registry** (`configs/models.yaml` + `utils/model_registry.py`): All model aliases resolve through YAML config. Three categories: `user_models`, `sonic_models`, `judge_models`. To add a new model, edit the YAML — no code changes needed. Unmatched aliases fall back to literal Bedrock model IDs.

**Tool registry** (`tools/tool_registry.py`): Decorator-based registration (`@registry.tool("name")`), async execution. If no handler is registered for a tool call, `main.py` falls back to a mock response. Config field `tool_registry_module` can point to an external module loaded at startup via `importlib.import_module()`. The module must expose a `registry` attribute.

**Polly TTS audio input** (`clients/polly_tts_client.py` + `logging_/conversation_audio_recorder.py`): When `input_mode: "polly"`, user simulator text is synthesized to 16kHz 16-bit mono PCM via Amazon Polly and streamed as real audio input to Nova Sonic. Audio is injected into the existing silence audio content block — `send_audio_input()` pauses silence, sends Polly chunks directly via `_send_raw_event()`, then resumes silence. When `record_conversation: true`, both input and output audio are recorded: input bytes are appended continuously (silence + Polly), output bytes are silence-padded using the input buffer length as the time reference. Saved as `input.lpcm`, `output.lpcm`, and `conversation.wav` (stereo L=input, R=output) in the session's `audio/` directory.

**Configuration** (`core/config_manager.py`): JSON/YAML configs with `.env` override support. Key dataclasses: `TestConfig`, `InferenceConfig`, `ToolConfig`. Legacy `claude_*` keys auto-migrate to `user_*`. Config field `log_judge_prompts` enables recording judge prompts in evaluation output. Config field `evaluation_criteria.rubrics` maps metric names to lists of rubric questions (used by binary judge). All logging (`log_audio`, `log_text`, `log_tools`, `log_events`) defaults to `true`; set to `false` in config to disable. Config field `dataset_path` points to a JSONL file or `hf://org/dataset` for dataset-driven testing. Config field `hangup_prompt_enabled` (default `true`) auto-appends a `[HANGUP]` instruction postamble to the user simulator's system prompt; skipped if the prompt already contains `[HANGUP]`.

**Dataset Loading** (`runners/dataset_loader.py`): `DatasetLoader` loads test datasets from local JSONL files or HuggingFace datasets. Each entry becomes a separate scripted session. Auto-detects two formats:

- **Turns array** (standard): `{"id": "...", "turns": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "...", "expected_tool_call": "..."}], "rubric": "...", "config": {...}}`
- **Wide format** (AudioMC-compatible): `{"id": "...", "user_turn_1_transcript": "...", "assistant_turn_1_transcript": "...", ...}`

`DatasetEntry` carries `user_messages`, `expected_responses`, `expected_tool_calls`, `rubric`, `category`, and optional `config_overrides`. Ground truth is passed to the LLM judge for comparison against actual responses.

### Evaluation System

**Binary Judge** (`evaluation/llm_judge_binary.py`):
- `BinaryLLMJudge` evaluates the **text transcript only** — it has no access to audio, timing, tone, or prosody
- 5 built-in metrics, each with multiple default rubric questions:
  - **Goal Achievement** (3 rubrics): goal identification, information delivery, resolution completeness
  - **Tool Usage** (4 rubrics, auto-skipped when no tools): correct tool selection, parameter accuracy, result interpretation, no redundant calls
  - **Response Quality** (3 rubrics): factual accuracy, relevance, voice-unsuitable formatting detection
  - **Conversation Flow** (3 rubrics): logical coherence, natural (non-robotic) exchange, no repetition
  - **System Prompt Compliance** (3 rubrics, auto-included when system prompt exists): role adherence, constraint following, scope enforcement
- Each metric returns strict YES/NO verdict with reasoning; metric passes only if ALL its rubric questions pass
- Config `evaluation_criteria.rubrics` overrides default rubric questions per metric
- Extensible: add entries to `BUILTIN_METRICS` dict or pass `custom_metrics` to constructor
- Returns `BinaryEvaluationResult` with compatibility fields (`overall_rating`="PASS"/"FAIL", `aspect_ratings`, `strengths`, `weaknesses`) so downstream consumers work unchanged
- Batch summary includes `metric_pass_rates` for per-metric aggregation

**Audio-Text Consistency** (`evaluation/evaluate_audio_text.py`):
- `AudioTextConsistencyJudge` compares Nova Sonic's text output against audio transcription (via Amazon Transcribe)
- `TranscriptionClient` (`clients/audio_transcription_client.py`) handles S3 upload, transcription polling, and cleanup
- Audio files are uploaded to S3 keyed by the turn's `sonic_session_id` (extracted from `completionStart` event), falling back to log session ID
- Per-turn verdicts: CONSISTENT, MINOR_DIFFERENCES, HALLUCINATION
- Produces `AudioTextConsistencyReport` with hallucination rate and per-turn results

**Evaluation Dashboard** (`evaluation/evaluation_dashboard.py`):
- Streamlit app: `streamlit run evaluation/evaluation_dashboard.py`
- Pages: Batch Overview (trend charts, KPIs), Batch Detail (per-session table with filters, export), Session Detail (chat view with audio playback, evaluation verdicts, session continuation events), Error Analysis (per-metric failure rates, co-failure correlation, drill-down), Batch Comparison (delta analysis, per-metric comparison), Search (full-text search across all session conversations)
- Data source: reads from `results/batches/` and `results/sessions/` directories
- Supports CSV/JSON export of filtered session data

**Config Generator** (`utils/generate_config.py`):
- CLI tool that generates test config JSON from natural language descriptions via an LLM
- Usage: `python -m utils.generate_config "description" --tools --turns 8 --voice tiffany`
- Supports generating multiple scenario variants with `--num-variants N`
- Can output to specific path with `--output` or defaults to `configs/<test_name>.json`

### Module Dependency Graph

```
main.py
├── core/sonic_stream_manager.py (RxPY, aws_sdk_bedrock_runtime)
├── core/session_continuation.py → core/conversation_history.py
├── clients/bedrock_model_client.py → utils/model_registry.py → configs/models.yaml
├── core/config_manager.py
├── logging_/interaction_logger.py
├── tools/tool_registry.py
├── logging_/results_manager.py
├── clients/polly_tts_client.py (Amazon Polly, optional)
├── logging_/conversation_audio_recorder.py (LPCM + WAV recording, optional)
├── logging_/event_logger.py (JSONL event logging, optional)
└── evaluation/llm_judge_binary.py → utils/model_registry.py

main.py → runners/multi_session_runner.py (for batch/parallel runs)
main.py → runners/dataset_loader.py (for --dataset runs)
runners/multi_session_runner.py → main.py (LiveInteractionSession), core/config_manager,
                                   logging_/results_manager, runners/dataset_loader
evaluation/evaluate_audio_text.py → clients/audio_transcription_client.py (Amazon Transcribe via S3)
                                  → utils/model_registry.py (judge model resolution)
```

## Data Formats

### Interaction Log (`results/sessions/{id}/logs/interaction_log.json`)

```json
{
  "session_id": "order_status_normal_20260225_144223_a1b2c3",
  "test_name": "order_status_normal",
  "start_time": "2026-02-25T14:42:23.261709",
  "end_time": "2026-02-25T14:47:17.065399",
  "configuration": { "...full TestConfig as dict..." },
  "turns": [
    {
      "turn_number": 1,
      "timestamp": "2026-02-25T14:42:25.000000",
      "user_message": "Hi, I'd like to check on my order ORD-2024-33100.",
      "sonic_response": "Of course! Let me look that up for you.",
      "sonic_user_transcription": "optional: Nova Sonic's transcription of audio input",
      "tool_calls": [
        {
          "tool_name": "lookupOrder",
          "tool_use_id": "uuid",
          "tool_input": { "order_id": "ORD-2024-33100" },
          "tool_result": { "status": "shipped" },
          "timestamp": "2026-02-25T14:42:30.000000"
        }
      ],
      "audio_recorded": true,
      "audio_file": "turn_1.wav",
      "sonic_session_id": "uuid"
    }
  ],
  "total_turns": 4,
  "total_tool_calls": 1,
  "errors": [],
  "session_events": [
    {
      "timestamp": "...",
      "event_type": "transition",
      "old_session_number": 1,
      "new_session_number": 2,
      "old_session_duration_seconds": 365.2,
      "messages_replayed": 6,
      "total_bytes_replayed": 4096,
      "after_turn_number": 3,
      "success": true
    }
  ],
  "summary": {
    "total_turns": 4,
    "total_tool_calls": 1,
    "tool_usage_rate": "25.00%",
    "avg_user_message_length": 236.5,
    "avg_sonic_response_length": 286.0,
    "session_continuations": 1
  }
}
```

### Batch Summary (`results/batches/{batch_id}/batch_summary.json`)

```json
{
  "batch_id": "batch_20260225_144223",
  "totals": { "total_sessions": 5, "completed": 5, "failed": 0, "evaluated": 5 },
  "evaluation_summary": {
    "pass_rate": 0.8,
    "pass_count": 4,
    "fail_count": 1,
    "metric_pass_rates": {
      "Goal Achievement": { "passed": 4, "total": 5, "rate": 0.8 }
    }
  },
  "sessions": [
    {
      "session_id": "...",
      "config_name": "...",
      "status": "completed",
      "evaluation": {
        "overall_rating": "PASS",
        "pass_fail": true,
        "pass_rate": 1.0,
        "aspect_ratings": { "Goal Achievement": "PASS" },
        "strengths": ["..."],
        "weaknesses": []
      }
    }
  ]
}
```

## Audio Parameters

- Input: 16kHz mono 16-bit PCM
- Output (text mode): 24kHz mono 16-bit PCM (default)
- Output (Polly mode): 16kHz mono 16-bit PCM (matches input for recording alignment)
- Output sample rate is set via `audioOutputConfiguration.sampleRateHertz` in `start_prompt()` — Nova Sonic respects this setting
- Polly TTS: 16kHz 16-bit mono PCM
- Text chunking: 1,000 chars per `textInput` event
- Audio chunking: 512-frame chunks
- Conversation recording outputs: `input.lpcm`, `output.lpcm`, `conversation.wav` (stereo L=input, R=output)

## Adding Custom Tools

```python
from tools.tool_registry import ToolRegistry
registry = ToolRegistry()

@registry.tool("getBookingDetails")
async def get_booking(tool_input):
    booking_id = tool_input.get("booking_id", "")
    return {"status": "success", "data": {...}}
```

The module must expose a `registry` attribute at module level. Point to it in config via `tool_registry_module` (Python dotted path, e.g., `"tools.order_status_tools"` or `"examples.healthcare_tools"`). See `examples/example_with_tool_registry.py` for full patterns.

## Skills (Reusable Task Guides)

This project includes step-by-step skill guides in `.kiro/skills/` for common tasks. **Before performing any of the tasks listed below, read the corresponding skill file and follow its instructions.** Each skill contains project-specific rules, validation steps, and reference files that produce better results than working from scratch.

### Config Generator — `.kiro/skills/config-generator/skill.md`

**When to use:** Creating, modifying, or cloning a test config JSON file.

Read this skill before generating any config. It will direct you to:
- `.kiro/skills/config-generator/schema_reference.md` — full `TestConfig` schema with all fields, types, and defaults
- `.kiro/skills/config-generator/model_aliases.md` — model alias table (never use raw Bedrock model IDs)

Key rules enforced by this skill: system prompts must be written for spoken delivery, evaluation aspects must use built-in metric names, tool names must match handler code, and `[HANGUP]` is never included in user prompts.

### Tool Builder — `.kiro/skills/tool-builder/skill.md`

**When to use:** Creating tool handler Python modules (mock implementations for test scenarios).

Read this skill before writing any tool module. It will direct you to:
- `.kiro/skills/tool-builder/tool_pattern.md` — reference implementation with exact module structure, decorator pattern, mock data layout, and standalone test function

Key rules enforced by this skill: module must expose `registry` at module level, tool names must exactly match `toolSpec.name` in the config, handlers must be async, and mock data must be realistic.

### Eval Analyzer — `.kiro/skills/eval-analyzer/skill.md`

**When to use:** Analyzing test results, debugging failures, comparing batches, or improving prompts based on evaluation feedback.

Read this skill before analyzing any evaluation data. It will direct you to:
- `.kiro/skills/eval-analyzer/data_formats.md` — JSON structures for batch indexes, batch summaries, session evaluations, and interaction logs

Key rules enforced by this skill: start from batch-level stats before drilling into sessions, check co-failure correlations, and always provide actionable recommendations with concrete prompt/config changes.

## Coding Conventions

- **Python 3.12** — use modern syntax (type unions with `|`, `match` statements where appropriate)
- **Async-first** — all tool handlers and streaming code use `async/await`
- **Dataclasses for config** — `TestConfig`, `InferenceConfig`, `ToolConfig` in `core/config_manager.py`
- **No hardcoded model IDs** — always use aliases from `configs/models.yaml`
- **Logging directory is `logging_/`** — trailing underscore to avoid shadowing Python's `logging` module
- **Results are structured** — each session gets its own directory under `results/sessions/`
- **Config-driven behavior** — prefer adding config fields over code flags; document new fields in the schema
- **Tool modules use `importlib`** — `main.py` dynamically imports tool registry modules; the module must have a `registry` attribute

## Files to Ignore

- `.venv/` — Python virtual environment
- `results/` — generated output, not checked in
- `__pycache__/` — Python bytecode cache
- `.env` — local credentials (`.env.example` is the template)
- `.DS_Store` — macOS metadata
