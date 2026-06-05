#!/usr/bin/env python3
"""
Config Generator — generates Nova Sonic test configs from a natural language use case.

Usage:
    python generate_config.py "customer calls to dispute a credit card charge"
    python generate_config.py "user asks about flight delays" --tools
    python generate_config.py "tech support for wifi issues" --output configs/wifi_support.json
    python generate_config.py "hotel concierge booking restaurants" --turns 8 --voice tiffany
    python generate_config.py "describe the scenario" --model claude-sonnet --num-variants 3
"""

import argparse
import json
import sys
from pathlib import Path

import boto3

from utils.model_registry import get_model_registry

GENERATOR_SYSTEM_PROMPT = """\
You are a test config generator for Nova Sonic, a speech-to-speech AI model.
Given a use case description, produce a SINGLE valid JSON config object.

The config must follow this schema exactly (all fields required):
{
  "test_name": "<snake_case name>",
  "description": "<1-line description>",
  "sonic_model_id": "nova-sonic",
  "sonic_system_prompt": "<detailed system prompt for the voice agent being tested>",
  "sonic_inference_config": {"max_tokens": 1024, "temperature": 0.7, "top_p": 0.9},
  "sonic_voice_id": "<voice>",
  "user_model_id": "claude-haiku",
  "user_system_prompt": "<detailed persona prompt for the simulated caller>",
  "user_max_tokens": 512,
  "user_temperature": 1.0,
  "interaction_mode": "bedrock",
  "scripted_messages": [],
  "max_turns": <int>,
  "turn_delay": 0,
  "log_audio": true,
  "log_text": true,
  "log_tools": true,
  "output_directory": "results",
  "auto_evaluate": true,
  "evaluation_criteria": {
    "user_goal": "<what the caller wants>",
    "assistant_objective": "<what the agent should do>",
    "evaluation_aspects": ["Goal Achievement", "Conversation Flow", "Response Quality", ...]
  }
}

Rules for sonic_system_prompt:
- Write it as instructions for a phone agent (spoken, not text-based)
- Include the agent's role, company/context, specific rules, and response style
- Tell it to keep responses to 1-2 short sentences (it's a voice agent)
- Tell it never to use markdown

Rules for user_system_prompt:
- Give the simulated user a name and persona
- Describe their goal, emotional state, and conversation style
- Tell them to keep messages to 1-2 sentences and speak naturally
- Do NOT include [HANGUP] instructions — the harness appends this automatically when hangup_prompt_enabled is true (default).

If tools are requested, also include:
  "sonic_tool_config": {
    "tools": [{"toolSpec": {"name": "...", "description": "...", "inputSchema": {"json": {"type": "object", "properties": {...}, "required": [...]}}}}],
    "tool_choice": {"auto": {}}
  }
And add "Tool Usage Appropriateness" to evaluation_aspects.
And add to sonic_system_prompt: rules about when/how to use each tool.

sonic_voice_id options: matthew, tiffany, amy. Pick one that fits the persona.

Output ONLY the JSON object. No markdown fences, no explanation."""


def generate_config(
    use_case: str,
    *,
    model: str = "claude-sonnet",
    include_tools: bool = False,
    max_turns: int = None,
    voice: str = None,
    num_variants: int = 1,
) -> list[dict]:
    """Generate config(s) from a use case description via LLM."""
    registry = get_model_registry()
    resolved = registry.resolve_judge_model(model)

    client = boto3.client("bedrock-runtime", region_name=resolved["region"])

    user_msg = f"Use case: {use_case}"
    if include_tools:
        user_msg += "\n\nInclude realistic tool definitions for this scenario."
    if max_turns:
        user_msg += f"\n\nSet max_turns to {max_turns}."
    if voice:
        user_msg += f"\n\nUse sonic_voice_id: {voice}."
    if num_variants > 1:
        user_msg += (
            f"\n\nGenerate {num_variants} DIFFERENT config variants as a JSON array. "
            "Vary the user persona (e.g. angry, confused, polite), scenario details, "
            "and edge cases. Each must be a complete config object."
        )

    response = client.converse(
        modelId=resolved["model_id"],
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        system=[{"text": GENERATOR_SYSTEM_PROMPT}],
        inferenceConfig={"maxTokens": 4096, "temperature": 0.8},
    )

    text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            text += block["text"]

    # Parse — handle both single object and array
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    parsed = json.loads(text)
    configs = parsed if isinstance(parsed, list) else [parsed]
    return configs


def main():
    parser = argparse.ArgumentParser(description="Generate Nova Sonic test configs from a use case description")
    parser.add_argument("use_case", help="Natural language description of the test scenario")
    parser.add_argument("--tools", action="store_true", help="Include tool definitions")
    parser.add_argument("--model", default="claude-sonnet", help="Generator model alias (default: claude-sonnet)")
    parser.add_argument("--turns", type=int, help="Override max_turns")
    parser.add_argument("--voice", help="Override sonic_voice_id (matthew/tiffany/amy)")
    parser.add_argument("--num-variants", type=int, default=1, help="Generate N scenario variants")
    parser.add_argument("--output", "-o", help="Output path (file or directory)")
    parser.add_argument("--dry-run", action="store_true", help="Print config(s) to stdout without saving")
    args = parser.parse_args()

    print(f"🧠 Generating config for: {args.use_case}")
    configs = generate_config(
        args.use_case,
        model=args.model,
        include_tools=args.tools,
        max_turns=args.turns,
        voice=args.voice,
        num_variants=args.num_variants,
    )
    print(f"✅ Generated {len(configs)} config(s)")

    if args.dry_run:
        print(json.dumps(configs if len(configs) > 1 else configs[0], indent=2))
        return

    # Determine output path(s)
    if args.output:
        out = Path(args.output)
        if len(configs) == 1:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(configs[0], indent=2))
            print(f"📄 Saved: {out}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            for cfg in configs:
                p = out / f"{cfg['test_name']}.json"
                p.write_text(json.dumps(cfg, indent=2))
                print(f"📄 Saved: {p}")
    else:
        # Default: configs/<test_name>.json
        for cfg in configs:
            p = Path("configs") / f"{cfg['test_name']}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cfg, indent=2))
            print(f"📄 Saved: {p}")


if __name__ == "__main__":
    main()
