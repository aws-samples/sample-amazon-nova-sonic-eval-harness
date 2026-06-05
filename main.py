"""
Main Interaction Loop for Live Mode
Orchestrates conversations between Claude (user) and Nova Sonic (assistant).
"""

import asyncio
import argparse
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

from core.sonic_stream_manager import SonicStreamManager
from core.session_continuation import SessionContinuationManager
from clients.bedrock_model_client import BedrockModelClient, ScriptedUser
from core.config_manager import ConfigManager, TestConfig
from logging_.interaction_logger import InteractionLogger
from tools.tool_registry import ToolRegistry
from utils.model_registry import get_model_registry
from logging_.results_manager import ResultsManager
from clients.polly_tts_client import PollyTTSClient
from logging_.conversation_audio_recorder import ConversationAudioRecorder
from logging_.event_logger import EventLogger

# Signal token that the user simulator can emit to end the conversation early
HANGUP_SIGNAL = "[HANGUP]"

# Optional: Import judge for automatic evaluation
try:
    from evaluation.llm_judge_binary import BinaryLLMJudge
    JUDGE_AVAILABLE = True
except ImportError:
    JUDGE_AVAILABLE = False

class LiveInteractionSession:
    """
    Manages a live interaction session between Claude and Nova Sonic.
    """

    def __init__(
        self,
        config: TestConfig,
        tool_registry: Optional[ToolRegistry] = None,
        auto_evaluate: bool = False,
        evaluation_criteria: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        expected_responses: Optional[list] = None,
        expected_tool_calls: Optional[list] = None,
        rubric: Optional[str] = None,
    ):
        """
        Initialize the interaction session.

        Args:
            config: Test configuration
            tool_registry: Optional ToolRegistry for custom tool implementations
            auto_evaluate: If True, automatically run LLM judge evaluation after conversation
            evaluation_criteria: Optional criteria for evaluation (user_goal, assistant_objective, aspects)
            session_id: Optional custom session ID (overrides auto-generated ID in logger)
            expected_responses: Ground truth expected responses per turn (from dataset)
            expected_tool_calls: Expected tool call names per turn (from dataset)
            rubric: Evaluation rubric text (from dataset)
        """
        self.config = config
        self.tool_registry = tool_registry
        self.auto_evaluate = auto_evaluate
        self.evaluation_criteria = evaluation_criteria
        self.session_id = session_id
        self.expected_responses = expected_responses
        self.expected_tool_calls = expected_tool_calls
        self.rubric = rubric
        self.logger: Optional[InteractionLogger] = None
        self.sonic: Optional[SonicStreamManager] = None
        self.claude: Optional[BedrockModelClient] = None
        self.scripted_user: Optional[ScriptedUser] = None

        # Evaluation result (populated after run_evaluation, read by multi_session_runner)
        self.evaluation_result: Optional[Dict[str, Any]] = None

        # Polly TTS and conversation recording
        self.polly_client: Optional[PollyTTSClient] = None
        self.conversation_recorder: Optional[ConversationAudioRecorder] = None

        # Event logging (debug mode)
        self.event_logger: Optional[EventLogger] = None

        # Turn tracking
        self.current_turn = 0
        self.sonic_response_buffer = ""
        self.waiting_for_response = False

        # Turn completion tracking (for proper turn-taking)
        self.speculative_text_count = 0
        self.final_text_count = 0
        self.turn_complete = False
        self.last_final_text_time = None
        self.current_generation_stage = None
        self.current_role = None

        # User transcription buffer (Polly mode: Nova Sonic echoes back transcribed text)
        self.user_transcription_buffer = ""

        # Nova Sonic session ID (from streaming events)
        self.current_sonic_session_id: Optional[str] = None

    def _handle_session_continuation(self, **kwargs):
        """Forward session continuation events to the interaction logger."""
        if self.logger:
            self.logger.add_session_continuation_event(**kwargs)

    async def setup(self):
        """Set up all components."""
        print(f"\n{'='*60}")
        print(f"Setting up Live Interaction Session")
        print(f"Test: {self.config.test_name}")
        print(f"{'='*60}\n")

        # Load tool registry from config if not explicitly provided
        if self.tool_registry is None:
            self.tool_registry = _load_tool_registry(self.config)

        # Initialize logger
        self.logger = InteractionLogger(
            test_name=self.config.test_name,
            output_dir=self.config.output_directory,
            log_audio=self.config.log_audio,
            log_text=self.config.log_text,
            log_tools=self.config.log_tools,
            session_id=self.session_id
        )
        self.logger.set_configuration(self.config.to_dict())
        self.logger.save_config(self.config.to_dict())

        # Initialize event logger if enabled
        if self.config.log_events:
            events_path = str(self.logger.logs_dir / "events.jsonl")
            self.event_logger = EventLogger(events_path)

        # Initialize user (Bedrock, Claude API, or scripted)
        if self.config.interaction_mode == "bedrock":
            print(f"🤖 Initializing {self.config.user_model_id} (Bedrock) as simulated user...")
            user_prompt = self.config.user_system_prompt or ""
            if self.config.hangup_prompt_enabled and HANGUP_SIGNAL not in user_prompt:
                hangup_postamble = f"\n\nWhen you have completed your goal or are satisfied with the conversation, end by saying only {HANGUP_SIGNAL}."
                user_prompt = user_prompt.rstrip() + hangup_postamble
            self.claude = BedrockModelClient(
                model=self.config.user_model_id,
                system_prompt=user_prompt,
                max_tokens=self.config.user_max_tokens,
                temperature=self.config.user_temperature
            )
        else:  # scripted
            print("📜 Using scripted user messages...")
            self.scripted_user = ScriptedUser(self.config.scripted_messages)

        # Initialize Nova Sonic
        print("🎤 Initializing Nova Sonic stream...")

        # Convert tool config to dict if needed
        tool_config_dict = None
        if self.config.sonic_tool_config:
            tool_config_dict = {
                "tools": self.config.sonic_tool_config.tools,
                "toolChoice": self.config.sonic_tool_config.tool_choice
            }
            print(f"🔧 Tool config loaded: {len(tool_config_dict.get('tools', []))} tools configured")
        else:
            print("⚠️  No tool configuration found in config")

        # Convert inference config to dict
        inference_config_dict = {
            "maxTokens": self.config.sonic_inference_config.max_tokens,
            "temperature": self.config.sonic_inference_config.temperature,
            "topP": self.config.sonic_inference_config.top_p
        }

        # Resolve Sonic model alias via registry
        registry = get_model_registry()
        sonic_resolved = registry.resolve_sonic_model(self.config.sonic_model_id)
        sonic_model_id = sonic_resolved["model_id"]
        sonic_region = self.config.sonic_region or sonic_resolved["region"]

        if self.config.enable_session_continuation:
            self.sonic = SessionContinuationManager(
                model_id=sonic_model_id,
                region=sonic_region,
                endpoint_uri=self.config.sonic_endpoint_uri,
                system_prompt=self.config.sonic_system_prompt,
                voice_id=self.config.sonic_voice_id,
                inference_config=inference_config_dict,
                tool_config=tool_config_dict,
                tool_handler=self.handle_tool_call,
                event_callback=self.handle_sonic_event,
                transition_threshold_seconds=self.config.transition_threshold_seconds,
                audio_buffer_duration_seconds=self.config.audio_buffer_duration_seconds,
                enable_continuation=True,
                continuation_callback=self._handle_session_continuation
            )
        else:
            self.sonic = SonicStreamManager(
                model_id=sonic_model_id,
                region=sonic_region,
                endpoint_uri=self.config.sonic_endpoint_uri,
                system_prompt=self.config.sonic_system_prompt,
                voice_id=self.config.sonic_voice_id,
                inference_config=inference_config_dict,
                tool_config=tool_config_dict,
                tool_handler=self.handle_tool_call,
                event_callback=self.handle_sonic_event
            )

        # Attach event logger BEFORE initialize_stream so init events are captured
        if self.event_logger:
            self.sonic.event_logger = self.event_logger

        await self.sonic.initialize_stream()

        # Initialize Polly TTS client if input_mode is "polly"
        if self.config.input_mode == "polly":
            polly_region = self.config.polly_region or self.config.sonic_region
            self.polly_client = PollyTTSClient(
                region=polly_region,
                voice_id=self.config.polly_voice_id,
                engine=self.config.polly_engine,
            )
            print(f"🔊 Polly TTS initialized (voice={self.config.polly_voice_id}, engine={self.config.polly_engine}, region={polly_region})")

        # Initialize conversation recorder (TTS / Polly mode only)
        if self.config.record_conversation and self.config.input_mode == "polly":
            self.conversation_recorder = ConversationAudioRecorder()
            self.conversation_recorder.start()
            self.sonic.input_audio_callback = lambda chunk: self.conversation_recorder.add_input_chunk(chunk)
            print("🎙️  Conversation recording enabled (LPCM)")

        print("✅ Setup complete\n")

    async def handle_sonic_event(self, event: Dict[str, Any]):
        """
        Handle events from Nova Sonic.

        Args:
            event: Event data from Sonic
        """
        event_type = event.get('type')

        # Track Nova Sonic session ID from any event
        sonic_sid = event.get('sonic_session_id')
        if sonic_sid:
            self.current_sonic_session_id = sonic_sid

        if event_type == 'content_start':
            # Track generation stage from contentStart
            generation_stage = event.get('generation_stage')
            if generation_stage:
                self.current_generation_stage = generation_stage
            role = event.get('role')
            if role:
                self.current_role = role
            

        elif event_type == 'text_output':
            text = event.get('content', '')

            # Capture USER role text (Nova Sonic's transcription of audio input)
            if self.current_role == "USER":
                self.user_transcription_buffer += text
                print(f"   [USER TRANSCRIPTION]: {text[:80]}..." if len(text) > 80 else f"   [USER TRANSCRIPTION]: {text}")

            # Process ASSISTANT text
            elif self.current_role == "ASSISTANT":
                # Count text stages for proper turn completion
                if self.current_generation_stage == 'SPECULATIVE':
                    self.speculative_text_count += 1
                    # Log but don't accumulate speculative text
                    print(f"   [SPECULATIVE #{self.speculative_text_count}]: {text[:50]}..." if len(text) > 50 else f"   [SPECULATIVE #{self.speculative_text_count}]: {text}")

                elif self.current_generation_stage == 'FINAL':
                    self.final_text_count += 1
                    self.last_final_text_time = time.time()

                    # Only accumulate FINAL text to response buffer
                    self.sonic_response_buffer += text
                    print(f"   [FINAL #{self.final_text_count}]: {text[:50]}..." if len(text) > 50 else f"   [FINAL #{self.final_text_count}]: {text}")

                    # Check if all texts are finalized (spec count matches final count)
                    if self.speculative_text_count > 0 and self.speculative_text_count == self.final_text_count:
                        print(f"   ✅ All speculative texts finalized ({self.speculative_text_count}=={self.final_text_count})")
                        self.turn_complete = True

        elif event_type == 'audio_output':
            audio_bytes = event.get('audio_bytes')
            if audio_bytes:
                # Per-turn audio logging
                if self.logger and self.logger.log_audio:
                    self.logger.add_audio_chunk(audio_bytes)
                # Full conversation recording
                if self.conversation_recorder:
                    self.conversation_recorder.add_output_chunk(audio_bytes)

        elif event_type == 'tool_use':
            # Tool call started
            if self.logger and self.logger.log_tools:
                tool_name = event.get('tool_name')
                tool_use_id = event.get('tool_use_id')
                content = event.get('content', {})

                # Extract input - filter out metadata fields
                metadata_keys = {'toolUseId', 'toolName', 'type'}
                tool_input = {k: v for k, v in content.items() if k not in metadata_keys}

                self.logger.add_tool_call(
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    tool_input=tool_input
                )

    async def handle_tool_call(
        self,
        tool_name: str,
        tool_content: Dict[str, Any],
        tool_use_id: str
    ) -> Dict[str, Any]:
        """
        Handle tool calls from Nova Sonic.

        Args:
            tool_name: Name of the tool
            tool_content: Tool content/parameters (the toolUse event object)
            tool_use_id: Tool use ID

        Returns:
            Tool result
        """
        print(f"⚙️  Handling tool call: {tool_name}")

        # Extract input - Nova Sonic sends parameters in tool_content
        tool_input_raw = tool_content.get('content', {})

        # Parse if it's a JSON string
        if isinstance(tool_input_raw, str):
            import json
            try:
                tool_input = json.loads(tool_input_raw)
            except json.JSONDecodeError:
                tool_input = {}
        else:
            tool_input = tool_input_raw

        print(f"   ✅ Tool input: {json.dumps(tool_input) if isinstance(tool_input, dict) else tool_input}")
        # Execute tool
        if self.tool_registry and self.tool_registry.is_registered(tool_name):
            print(f"   ✅ Using registered implementation for {tool_name}")
            tool_result = await self.tool_registry.execute(tool_name, tool_input)
        else:
            # Fall back to mock implementation
            print(f"   ⚠️  Using mock implementation for {tool_name}")
            await asyncio.sleep(0.5)  # Simulate API delay

            tool_result = {
                "status": "success",
                "message": f"Mock result from {tool_name}",
                "data": {
                    "tool": tool_name,
                    "input": tool_input,
                    "timestamp": "2025-01-01T00:00:00Z",
                    "note": "This is a mock response. Register a real implementation using ToolRegistry."
                }
            }

        # Log the result
        if self.logger:
            self.logger.update_tool_result(tool_use_id, tool_result)

        return tool_result

    async def run_turn(self, user_message: str):
        """
        Run a single conversation turn.

        Args:
            user_message: The user's message
        """
        self.current_turn += 1

        # Start turn in logger
        if self.logger:
            self.logger.start_turn(user_message)

        # Reset turn tracking
        self.sonic_response_buffer = ""
        self.user_transcription_buffer = ""
        self.waiting_for_response = True
        self.speculative_text_count = 0
        self.final_text_count = 0
        self.turn_complete = False
        self.last_final_text_time = None
        self.current_generation_stage = None
        self.current_role = None

        # Send message to Sonic (Polly audio or text)
        if self.polly_client:
            try:
                pcm_audio = self.polly_client.synthesize(user_message)
                print(f"🔊 Polly synthesized {len(pcm_audio)} bytes for turn {self.current_turn}")
                if hasattr(self.sonic, 'send_audio_input'):
                    await self.sonic.send_audio_input(pcm_audio, text_for_history=user_message)
                else:
                    await self.sonic.send_audio_input(pcm_audio)
            except Exception as e:
                print(f"⚠️  Polly synthesis failed, falling back to text: {e}")
                await self.sonic.send_text_message(user_message)
        else:
            await self.sonic.send_text_message(user_message)

        # Wait for turn completion with two-phase timeouts:
        #   1) 120s for Nova Sonic to produce any response at all
        #   2) 120s between each consecutive FINAL text (resets on every new FINAL)
        response_timeout = 120.0  # Max wait for first response
        final_stall_timeout = 120.0  # Max gap between consecutive FINAL texts
        start_time = time.time()

        while not self.turn_complete and self.waiting_for_response:
            # Bail immediately if the underlying stream died (avoids 120s timeout)
            if not self.sonic.is_active:
                print(f"⚠️  Stream is no longer active — aborting turn {self.current_turn}")
                if self.logger:
                    self.logger.add_error(f"Stream died during turn {self.current_turn}")
                break

            has_any_response = self.speculative_text_count > 0 or self.final_text_count > 0

            # Phase 1: No response yet — timeout if Nova Sonic is silent
            if not has_any_response and (time.time() - start_time) > response_timeout:
                print(f"⚠️  Response timeout ({response_timeout}s) - no response from Nova Sonic")
                if self.logger:
                    self.logger.add_error(f"Response timeout - no response after {response_timeout}s")
                break

            # Primary completion signal: spec count matches final count
            if (self.speculative_text_count > 0 and
                self.final_text_count > 0 and
                self.speculative_text_count == self.final_text_count):
                self.turn_complete = True
                self.waiting_for_response = False
                break

            # Phase 2: Response started — timeout if no new FINAL text arrives
            if self.last_final_text_time and self.final_text_count > 0:
                time_since_last_final = time.time() - self.last_final_text_time
                if time_since_last_final > final_stall_timeout:
                    print(f"⚠️  No new FINAL text for {final_stall_timeout}s - SPEC={self.speculative_text_count}, FINAL={self.final_text_count}")
                    self.turn_complete = True
                    self.waiting_for_response = False
                    break

            await asyncio.sleep(0.1)

        # Log completion reason for debugging
        if self.turn_complete:
            if self.speculative_text_count == self.final_text_count and self.speculative_text_count > 0:
                print(f"✅ Turn complete: All texts finalized (SPEC={self.speculative_text_count}, FINAL={self.final_text_count})")
            else:
                print(f"✅ Turn complete: END_TURN signal or timeout")

        # Add Sonic's response to logger
        if self.logger:
            self.logger.add_sonic_response(self.sonic_response_buffer)
            # Tag the turn with the Nova Sonic session ID and user transcription
            if self.logger.current_turn:
                if self.current_sonic_session_id:
                    self.logger.current_turn.sonic_session_id = self.current_sonic_session_id
                if self.user_transcription_buffer:
                    self.logger.current_turn.sonic_user_transcription = self.user_transcription_buffer
            self.logger.end_turn()

        # Notify session continuation manager that the turn is complete
        if hasattr(self.sonic, 'mark_turn_complete'):
            await self.sonic.mark_turn_complete(self.sonic_response_buffer)

    async def run_conversation(self):
        """Run the full conversation."""
        print(f"\n{'='*60}")
        print(f"Starting Conversation")
        print(f"Max Turns: {self.config.max_turns}")
        print(f"{'='*60}\n")

        # Initial context for user simulator
        initial_context = "Start a conversation with the assistant. Ask a question or make a request."

        for turn in range(self.config.max_turns):
            # Abort remaining turns if the stream is dead
            if not self.sonic.is_active:
                print(f"⚠️  Stream is no longer active — ending conversation after {self.current_turn} turns")
                if self.logger:
                    self.logger.add_error(f"Stream died — conversation ended early after {self.current_turn} turns")
                break

            # Generate user message
            if self.config.interaction_mode == "bedrock":
                # Use Bedrock model to generate user message (synchronous boto3 call)
                if turn == 0:
                    user_message = self.claude.generate_response(initial_context)
                else:
                    # Generate based on previous Sonic response
                    context = "Continue the conversation naturally based on the assistant's response. You can ask follow-up questions, request clarification, or introduce new topics."
                    user_message = self.claude.generate_response(
                        context,
                        assistant_message=self.sonic_response_buffer
                    )
            else:
                # Use scripted message
                user_message = self.scripted_user.get_next_message()
                if user_message is None:
                    print("📜 Scripted conversation complete")
                    break

            # Check if the user simulator signaled a hangup
            if HANGUP_SIGNAL in user_message:
                reason = "Customer hung up"
                print(f"\n📞 {reason} (turn {turn + 1})")
                if self.logger:
                    self.logger.add_event("hangup", {"reason": reason, "turn": turn + 1})
                break

            # Run the turn
            await self.run_turn(user_message)

            # Delay between turns (skip after the last turn)
            if self.config.turn_delay > 0 and turn < self.config.max_turns - 1:
                await asyncio.sleep(self.config.turn_delay)

        print(f"\n{'='*60}")
        print(f"Conversation Complete")
        print(f"Total Turns: {self.current_turn}")
        print(f"{'='*60}\n")

        # Save full conversation recording (LPCM + WAV) if enabled
        if self.conversation_recorder and self.logger:
            audio_dir = str(self.logger.session_dir / "audio")                                                                                                                                                                                                                                                                                                                                           
            result = self.conversation_recorder.save(audio_dir)                                                                                                                                                                                                                                                                                                                                          
            if result:
                input_path, output_path, wav_path = result
                print(f"🎙️  Conversation input  saved: {input_path}")
                print(f"🎙️  Conversation output saved: {output_path}")
                print(f"🎙️  Conversation wav    saved: {wav_path}")                                                                                                                                                                                                                                                                                                                                   
            else:
                print("⚠️   No audio data to save for conversation recording")

        # Finalize logger to save the interaction log
        if self.logger:
            self.logger.finalize()

        # Close the stream now — evaluation only needs the saved log, not the stream
        await self.cleanup()

        # Fire off evaluation as a background task (doesn't block the session slot)
        if self.auto_evaluate:
            self._eval_task = asyncio.create_task(self.run_evaluation())

    async def await_evaluation(self):
        """Wait for the background evaluation task to finish.

        Returns the evaluation_result dict, or None if eval was not started.
        Called by multi_session_runner after all conversations complete.
        """
        task = getattr(self, '_eval_task', None)
        if task is not None:
            try:
                await task
            except Exception as e:
                print(f"⚠️  Evaluation failed: {e}")
        return self.evaluation_result

    async def run_evaluation(self):
        """Run binary LLM judge evaluation on the conversation."""
        if not JUDGE_AVAILABLE:
            print("⚠️  Binary Judge not available - skipping evaluation")
            print("   Check llm_judge_binary.py")
            return

        if not self.logger or not self.logger.session_dir:
            print("⚠️  No interaction log available - skipping evaluation")
            return

        print(f"\n{'='*60}")
        print(f"Running Binary Judge Evaluation")
        print(f"{'='*60}\n")

        try:
            # Use in-memory log instead of reading from disk
            conversation_log = self.logger.get_log().to_dict()

            DEFAULT_ASPECTS = ['Goal Achievement', 'Conversation Flow', 'Tool Usage Appropriateness', 'Response Accuracy', 'User Experience']

            # Prepare evaluation criteria (hierarchy: explicit > config file > system prompt fallback)
            if self.evaluation_criteria:
                user_goal = self.evaluation_criteria.get('user_goal', 'Complete the conversation successfully')
                assistant_objective = self.evaluation_criteria.get('assistant_objective', 'Provide helpful and accurate responses')
                evaluation_aspects = self.evaluation_criteria.get('evaluation_aspects', list(DEFAULT_ASPECTS))
            elif self.config.evaluation_criteria:
                ec = self.config.evaluation_criteria
                user_goal = ec.get('user_goal', f'Complete the test scenario: {self.config.test_name}')
                assistant_objective = ec.get('assistant_objective', 'Provide accurate, helpful responses and use tools appropriately')
                evaluation_aspects = ec.get('evaluation_aspects', list(DEFAULT_ASPECTS))
            else:
                # Fall back to system prompts
                user_goal = self.config.user_system_prompt if self.config.user_system_prompt else "Have a helpful conversation"
                assistant_objective = self.config.sonic_system_prompt if self.config.sonic_system_prompt else "Provide helpful assistance"
                evaluation_aspects = ['Goal Achievement', 'Response Accuracy', 'Conversation Flow', 'Voice Formatting']

                if self.config.sonic_tool_config:
                    evaluation_aspects.append('Tool Usage')
                if self.config.sonic_system_prompt:
                    evaluation_aspects.append('System Prompt Compliance')

            print(f"📋 Evaluation Criteria:")
            print(f"   User Goal: {user_goal}")
            print(f"   Assistant Objective: {assistant_objective}")
            print(f"   Aspects: {', '.join(evaluation_aspects)}\n")

            # Build eval config shared across judge types
            from dataclasses import asdict as _asdict
            tool_config_dict = None
            if self.config.sonic_tool_config:
                tool_config_dict = _asdict(self.config.sonic_tool_config) if hasattr(self.config.sonic_tool_config, '__dataclass_fields__') else self.config.sonic_tool_config
            eval_config = {
                "sonic_model_id": self.config.sonic_model_id,
                "user_model_id": self.config.user_model_id,
                "sonic_system_prompt": self.config.sonic_system_prompt or "",
                "sonic_tool_config": tool_config_dict,
            }

            await self._run_binary_evaluation(
                    conversation_log, user_goal, assistant_objective,
                    evaluation_aspects, eval_config,
                )

        except Exception as e:
            print(f"❌ Error during evaluation: {e}")
            import traceback
            traceback.print_exc()

    async def _run_binary_evaluation(
        self,
        conversation_log: Dict[str, Any],
        user_goal: str,
        assistant_objective: str,
        evaluation_aspects: list,
        eval_config: Dict[str, Any],
    ):
        """Run binary (YES/NO) judge evaluation."""
        from evaluation.evaluation_types import BinaryEvaluationCriteria

        # Extract rubrics from evaluation criteria config
        rubrics: Dict[str, list] = {}
        ec = self.evaluation_criteria or self.config.evaluation_criteria or {}
        if isinstance(ec, dict):
            rubrics = ec.get("rubrics", {})

        criteria = BinaryEvaluationCriteria(
            user_goal=user_goal,
            assistant_objective=assistant_objective,
            evaluation_aspects=evaluation_aspects,
            rubrics=rubrics,
        )

        judge = BinaryLLMJudge(judge_model="claude-opus", log_prompts=self.config.log_judge_prompts)

        result = judge.evaluate_conversation(
            conversation_log=conversation_log,
            criteria=criteria,
            config=eval_config,
            expected_responses=self.expected_responses,
            expected_tool_calls=self.expected_tool_calls,
            rubric=self.rubric,
        )

        # Print binary-formatted results
        print(f"{'='*60}")
        print("Evaluation Results (Binary)")
        print(f"{'='*60}\n")
        print(f"Overall: {'PASS' if result.pass_fail else 'FAIL'} ({result.pass_rate:.0%} metrics passed)")
        print(f"{result.summary}\n")

        print("Metric Verdicts:")
        for name, mv in result.metric_verdicts.items():
            verdict_str = "YES" if mv.verdict else "NO"
            print(f"  {'✅' if mv.verdict else '❌'} {name}: {verdict_str}")
            if mv.rubric_verdicts:
                for rv in mv.rubric_verdicts:
                    rv_str = "YES" if rv.verdict else "NO"
                    print(f"      {'✓' if rv.verdict else '✗'} [{rv_str}] {rv.question}")

        # Store evaluation result for programmatic access (compatibility keys)
        self.evaluation_result = {
            "overall_rating": result.overall_rating,
            "aspect_ratings": result.aspect_ratings,
            "pass_fail": result.pass_fail,
            "pass_rate": result.pass_rate,
            "strengths": result.strengths,
            "weaknesses": result.weaknesses,
        }

        # Save evaluation results
        eval_path = self.logger.evaluation_dir / 'llm_judge_evaluation.json'
        judge.save_evaluation(
            result=result,
            output_path=eval_path,
            criteria=criteria,
            conversation_log=conversation_log,
        )
        print(f"\n💾 Evaluation saved to: {eval_path}")


    async def cleanup(self):
        """Clean up resources with a hard timeout to prevent hanging.
        Safe to call multiple times.
        """
        if getattr(self, '_cleaned_up', False):
            return
        self._cleaned_up = True

        print("\n🔄 Cleaning up...")

        # Close Sonic stream (with its own internal 10s timeout)
        if self.sonic:
            try:
                await asyncio.wait_for(self.sonic.close(), timeout=15.0)
            except asyncio.TimeoutError:
                print("⚠️  Sonic close timed out — forcing shutdown")
            except Exception as e:
                print(f"⚠️  Error closing sonic: {e}")

        # Close event logger
        if self.event_logger:
            self.event_logger.close()

        # Finalize logger if not already finalized (for error cases)
        if self.logger and self.logger.log.end_time is None:
            self.logger.finalize()

        print("✅ Cleanup complete\n")

    def _organize_results(self, batch_id: Optional[str] = None):
        """Register session in the results index (files already written by logger)."""
        if not self.logger or not self.logger.session_dir:
            return
        try:
            rm = ResultsManager(base_results_dir=str(self.logger.output_dir))
            rm.register_session(
                session_id=self.logger.session_id,
                test_name=self.config.test_name,
                session_dir=self.logger.session_dir,
                batch_id=batch_id,
            )
        except Exception as e:
            print(f"⚠️  Failed to register results: {e}")

    async def run(self, batch_id: Optional[str] = None):
        """Run the complete interaction session."""
        try:
            await self.setup()
            await self.run_conversation()
            self._organize_results(batch_id=batch_id)
        except KeyboardInterrupt:
            print("\n\n⚠️  Session interrupted by user")
            await self.cleanup()
        except Exception as e:
            print(f"\n❌ Error during session: {e}")
            import traceback
            traceback.print_exc()
            if self.logger:
                self.logger.add_error(str(e))
            await self.cleanup()


# Allowed package prefixes for tool registry modules. Only modules under these
# top-level packages can be loaded via the tool_registry_module config field.
# Add new prefixes here when introducing additional tool module directories.
ALLOWED_TOOL_MODULE_PREFIXES = (
    "tools.",
    "examples.",
    "custom_tools.",
    "scenarios.",
)


def _load_tool_registry(config: TestConfig) -> Optional[ToolRegistry]:
    """Load tool registry from module specified in config.

    Only modules whose dotted path starts with one of ALLOWED_TOOL_MODULE_PREFIXES
    are permitted. This prevents arbitrary code execution via config injection.
    """
    if not config.tool_registry_module:
        return None

    module_path = config.tool_registry_module

    # Validate against allowlist to prevent arbitrary code loading
    if not module_path.startswith(ALLOWED_TOOL_MODULE_PREFIXES):
        print(
            f"❌ Refused to load tool registry module '{module_path}': "
            f"must start with one of {ALLOWED_TOOL_MODULE_PREFIXES}"
        )
        return None

    # Reject path traversal attempts (e.g., "examples..os" or embedded slashes)
    if ".." in module_path or "/" in module_path or "\\" in module_path:
        print(f"❌ Refused to load tool registry module '{module_path}': invalid module path")
        return None

    try:
        print(f"📦 Loading tool registry from: {module_path}")
        import importlib
        module = importlib.import_module(module_path)
        if hasattr(module, 'registry'):
            registry = module.registry
            print(f"✅ Loaded tool registry with tools: {', '.join(registry.list_tools())}")
            return registry
        else:
            print(f"⚠️  Module {module_path} has no 'registry' attribute")
    except ImportError as e:
        print(f"⚠️  Could not import tool registry module: {e}")
    except Exception as e:
        print(f"⚠️  Error loading tool registry: {e}")
    return None


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Nova Sonic Test Harness')
    subparsers = parser.add_subparsers(dest='command')

    # -----------------------------------------------------------------------
    # Subcommand: run (default — also triggered when no subcommand given)
    # -----------------------------------------------------------------------
    run_parser = subparsers.add_parser('run', help='Run test conversations')
    _add_run_args(run_parser)

    # -----------------------------------------------------------------------
    # Subcommand: evaluate
    # -----------------------------------------------------------------------
    eval_parser = subparsers.add_parser('evaluate', help='Evaluate an existing conversation log')
    eval_parser.add_argument('log_dir', type=str, help='Path to session log directory')
    eval_parser.add_argument('--user-goal', type=str, help='Custom user goal')
    eval_parser.add_argument('--assistant-objective', type=str, help='Custom assistant objective')
    eval_parser.add_argument('--aspects', type=str,
                             help='Comma-separated evaluation aspects (e.g. "Goal Achievement,Tool Usage")')
    eval_parser.add_argument('--rubrics', type=str,
                             help='JSON dict of rubric questions per metric')

    # -----------------------------------------------------------------------
    # Subcommand: evaluate-batch
    # -----------------------------------------------------------------------
    eval_batch_parser = subparsers.add_parser('evaluate-batch',
                                               help='Evaluate all unevaluated sessions in a batch and rebuild summary')
    eval_batch_parser.add_argument('batch_id', type=str, help='Batch ID (e.g. batch_20260423_234559)')
    eval_batch_parser.add_argument('--parallel', type=int, default=4,
                                   help='Number of parallel evaluations (default: 4)')
    eval_batch_parser.add_argument('--force', action='store_true',
                                   help='Re-evaluate sessions that already have results')
    eval_batch_parser.add_argument('--results-dir', type=str, default='results',
                                   help='Base results directory (default: results)')

    # -----------------------------------------------------------------------
    # Subcommand: evaluate-audio
    # -----------------------------------------------------------------------
    audio_parser = subparsers.add_parser('evaluate-audio',
                                         help='Check audio-text consistency (hallucination detection)')
    audio_parser.add_argument('--log', type=str, required=True, help='Path to session log directory')
    audio_parser.add_argument('--s3-bucket', type=str, required=True,
                              help='S3 bucket for temp audio uploads')
    audio_parser.add_argument('--region', type=str, default='us-east-1', help='AWS region')
    audio_parser.add_argument('--judge-model', type=str, default='claude-opus',
                              help='Judge model alias')

    # -----------------------------------------------------------------------
    # Parse — if no subcommand, treat all args as 'run'
    # -----------------------------------------------------------------------
    # Check if the first non-flag arg is a known subcommand
    known_commands = {'run', 'evaluate', 'evaluate-audio', 'evaluate-batch'}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands and sys.argv[1] not in ('-h', '--help') and not sys.argv[1].startswith('-'):
        # First arg is not a subcommand (e.g., old-style usage) — shouldn't happen,
        # but fall through to argparse error
        pass
    elif len(sys.argv) > 1 and (sys.argv[1] in known_commands or sys.argv[1] in ('-h', '--help')):
        # Explicit subcommand or --help
        pass
    else:
        # No subcommand given (starts with --flag) — inject 'run'
        sys.argv.insert(1, 'run')

    args = parser.parse_args()

    if args.command == 'evaluate':
        await _cmd_evaluate(args)
    elif args.command == 'evaluate-batch':
        await _cmd_evaluate_batch(args)
    elif args.command == 'evaluate-audio':
        _cmd_evaluate_audio(args)
    else:
        await _cmd_run(args)


def _add_run_args(parser):
    """Add arguments for the 'run' subcommand."""
    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument('--config', type=str,
                              help='Path to a single configuration file')
    config_group.add_argument('--scenarios-dir', type=str,
                              help='Directory containing scenario configurations')
    config_group.add_argument('--dataset', type=str,
                              help='Path to JSONL dataset or hf://org/dataset')

    parser.add_argument('--pattern', type=str, default='*.json',
                        help='Glob pattern for config files (default: *.json)')
    parser.add_argument('--test-name', type=str, help='Override test name')
    parser.add_argument('--max-turns', type=int, help='Override max turns')
    parser.add_argument('--mode', choices=['bedrock', 'scripted'],
                        help='Override interaction mode')
    parser.add_argument('--parallel', type=int, default=1,
                        help='Number of parallel sessions')
    parser.add_argument('--repeat', type=int, default=1,
                        help='Number of times to repeat each scenario')
    parser.add_argument('--no-evaluate', action='store_true',
                        help='Disable automatic evaluation')
    parser.add_argument('--log-judge-prompts', action='store_true',
                        help='Log judge prompts and raw responses in evaluation output')
    parser.add_argument('--sonic-voice', type=str,
                        help='Nova Sonic output voice ID (e.g. matthew, tiffany, amy)')
    parser.add_argument('--input-mode', choices=['text', 'polly'],
                        help='Audio input mode: text (default) or polly (TTS)')
    parser.add_argument('--polly-voice', type=str,
                        help='Polly voice ID (e.g. Matthew, Joanna)')
    parser.add_argument('--record-conversation', action='store_true',
                        help='Record full conversation as stereo WAV (L=input, R=output)')
    parser.add_argument('--output-dir', type=str,
                        help='Output directory for results (default: results)')
    parser.add_argument('--log-events', action='store_true',
                        help='Log all streaming events to JSONL (debug mode)')
    parser.add_argument('--base-config', type=str,
                        help='Base configuration file for dataset runs (used with --dataset)')


async def _cmd_evaluate(args):
    """Handle the 'evaluate' subcommand."""
    from evaluation.evaluate_log import evaluate_log
    import json as _json

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(f"Error: Directory not found: {log_dir}")
        sys.exit(1)

    evaluation_aspects = None
    if args.aspects:
        evaluation_aspects = [a.strip() for a in args.aspects.split(',')]

    rubrics = None
    if args.rubrics:
        rubrics = _json.loads(args.rubrics)

    await evaluate_log(
        log_dir,
        user_goal=args.user_goal,
        assistant_objective=args.assistant_objective,
        evaluation_aspects=evaluation_aspects,
        rubrics=rubrics,
    )


async def _cmd_evaluate_batch(args):
    """Handle the 'evaluate-batch' subcommand — evaluate all sessions in a batch."""
    import asyncio as _asyncio
    import json as _json
    from datetime import datetime as _dt
    from evaluation.evaluate_log import evaluate_log

    base = Path(args.results_dir)
    batch_file = base / "batches" / args.batch_id / "batch_summary.json"

    if not batch_file.exists():
        print(f"Error: Batch summary not found: {batch_file}")
        sys.exit(1)

    with open(batch_file) as f:
        summary = _json.load(f)

    sessions = summary.get("sessions", [])
    to_evaluate = []

    for entry in sessions:
        if entry.get("status") != "completed":
            continue
        if not args.force and "evaluation" in entry:
            continue

        session_id = entry["session_id"]
        session_dir = base / "sessions" / session_id

        # Skip if already has eval file on disk (unless --force)
        if not args.force:
            has_eval = (
                (session_dir / "evaluation" / "llm_judge_evaluation.json").exists()
                or (session_dir / "llm_judge_evaluation.json").exists()
            )
            if has_eval:
                continue

        # Must have an interaction log
        has_log = (
            (session_dir / "logs" / "interaction_log.json").exists()
            or (session_dir / "interaction_log.json").exists()
        )
        if not has_log:
            continue

        to_evaluate.append((session_id, session_dir))

    total = len(to_evaluate)
    already_done = len(sessions) - total
    print(f"\nBatch: {args.batch_id}")
    print(f"Total sessions: {len(sessions)} | Already evaluated: {already_done} | To evaluate: {total}\n")

    if total == 0:
        print("Nothing to evaluate. Use --force to re-evaluate existing results.")
    else:
        # Evaluate in parallel batches
        sem = _asyncio.Semaphore(args.parallel)
        evaluated = 0
        failed = 0

        async def eval_one(session_id, session_dir):
            nonlocal evaluated, failed
            async with sem:
                try:
                    print(f"  [{evaluated + failed + 1}/{total}] {session_id}")
                    await evaluate_log(session_dir)
                    evaluated += 1
                except Exception as e:
                    print(f"  ⚠ Failed {session_id}: {e}")
                    failed += 1

        tasks = [eval_one(sid, sdir) for sid, sdir in to_evaluate]
        await _asyncio.gather(*tasks)

        print(f"\nEvaluation complete: {evaluated} succeeded, {failed} failed")

    # Rebuild batch summary from disk eval files
    print("\nRebuilding batch summary...")
    for entry in sessions:
        if entry.get("status") != "completed":
            continue

        session_id = entry["session_id"]
        session_dir = base / "sessions" / session_id

        eval_path = session_dir / "evaluation" / "llm_judge_evaluation.json"
        if not eval_path.exists():
            eval_path = session_dir / "llm_judge_evaluation.json"
        if not eval_path.exists():
            continue

        with open(eval_path) as f:
            eval_data = _json.load(f)

        entry["evaluation"] = {
            "overall_rating": eval_data.get("results", {}).get("overall_rating", "UNKNOWN"),
            "pass_fail": eval_data.get("results", {}).get("pass_fail", False),
            "pass_rate": eval_data.get("results", {}).get("pass_rate", 0.0),
            "aspect_ratings": eval_data.get("results", {}).get("aspect_ratings", {}),
            "strengths": eval_data.get("results", {}).get("strengths", []),
            "weaknesses": eval_data.get("results", {}).get("weaknesses", []),
        }

    # Recompute totals
    evaluated_sessions = [s for s in sessions if "evaluation" in s]
    pass_count = sum(1 for s in evaluated_sessions if s["evaluation"].get("pass_fail"))
    fail_count = len(evaluated_sessions) - pass_count
    pass_rate = pass_count / len(evaluated_sessions) if evaluated_sessions else 0.0

    metric_pass_rates = {}
    for s in evaluated_sessions:
        for aspect, rating in s["evaluation"].get("aspect_ratings", {}).items():
            if aspect not in metric_pass_rates:
                metric_pass_rates[aspect] = {"total": 0, "passed": 0}
            metric_pass_rates[aspect]["total"] += 1
            if rating == "PASS":
                metric_pass_rates[aspect]["passed"] += 1

    for counts in metric_pass_rates.values():
        counts["rate"] = round(counts["passed"] / counts["total"], 3) if counts["total"] > 0 else 0.0

    summary["totals"]["evaluated"] = len(evaluated_sessions)
    summary["evaluation_summary"] = {
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": round(pass_rate, 3),
        "metric_pass_rates": metric_pass_rates,
    }
    summary["generated_at"] = _dt.now().isoformat()

    with open(batch_file, "w") as f:
        _json.dump(summary, f, indent=2)

    print(f"\nBatch summary updated: {batch_file}")
    print(f"Evaluated: {len(evaluated_sessions)}/{len(sessions)} | Pass: {pass_count} | Fail: {fail_count} | Rate: {pass_rate:.1%}")


def _cmd_evaluate_audio(args):
    """Handle the 'evaluate-audio' subcommand."""
    from evaluation.evaluate_audio_text import run_evaluation

    log_dir = Path(args.log)
    if not log_dir.exists():
        print(f"Error: Directory not found: {log_dir}")
        sys.exit(1)

    run_evaluation(log_dir, args.s3_bucket, args.region, args.judge_model)


async def _cmd_run(args):
    """Handle the 'run' subcommand (default)."""

    # --- Dataset execution path ---
    if args.dataset:
        from runners.multi_session_runner import MultiSessionRunner
        from runners.dataset_loader import DatasetLoader

        # Load base config (optional)
        base_config = None
        if args.base_config:
            config_manager = ConfigManager()
            base_config = config_manager.load_config(args.base_config)
            print(f"✅ Loaded base configuration from {args.base_config}")

        # Apply CLI overrides to base config
        if base_config:
            if args.test_name:
                base_config.test_name = args.test_name
            if args.max_turns is not None:
                base_config.max_turns = args.max_turns
            if args.no_evaluate:
                base_config.auto_evaluate = False
            if args.log_judge_prompts:
                base_config.log_judge_prompts = True
            if args.sonic_voice:
                base_config.sonic_voice_id = args.sonic_voice
            if args.input_mode:
                base_config.input_mode = args.input_mode
            if args.polly_voice:
                base_config.polly_voice_id = args.polly_voice
            if args.record_conversation:
                base_config.record_conversation = True
            if args.output_dir:
                base_config.output_directory = args.output_dir
            if args.log_events:
                base_config.log_events = True

        # Load dataset
        loader = DatasetLoader()
        entries = loader.load(args.dataset)
        if not entries:
            print("❌ No entries found in dataset")
            sys.exit(1)

        output_dir = args.output_dir or (base_config.output_directory if base_config else 'results')
        runner = MultiSessionRunner(
            results_manager=ResultsManager(base_results_dir=output_dir),
            auto_evaluate=not args.no_evaluate,
            parallel_sessions=args.parallel,
            log_judge_prompts=args.log_judge_prompts,
        )

        await runner.run_dataset(
            entries=entries,
            base_config=base_config,
        )

        runner.results_manager.generate_summary_report()
        if runner.last_batch_id:
            batch_path = runner.results_manager.batches_dir / runner.last_batch_id / "batch_summary.json"
            print(f"Batch summary: {batch_path}")
        return

    # Build config file list
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"❌ Configuration file not found: {config_path}")
            sys.exit(1)
        config_files = [config_path]
    else:
        scenarios_dir = Path(args.scenarios_dir)
        if not scenarios_dir.exists():
            print(f"❌ Scenarios directory not found: {scenarios_dir}")
            sys.exit(1)
        config_files = sorted(scenarios_dir.rglob(args.pattern))
        if not config_files:
            print(f"❌ No config files found matching pattern: {args.pattern}")
            sys.exit(1)

    # Repeat scenarios if requested
    if args.repeat > 1:
        config_files = [cf for cf in config_files for _ in range(args.repeat)]

    # Determine if we need the multi-session runner
    use_batch_runner = len(config_files) > 1 or args.parallel > 1

    if use_batch_runner:
        # Multi-session path: parallel, repeat, or multiple configs
        from runners.multi_session_runner import MultiSessionRunner

        # Build config overrides from CLI args
        config_overrides = {}
        if args.test_name:
            config_overrides['test_name'] = args.test_name
        if args.max_turns is not None:
            config_overrides['max_turns'] = args.max_turns
        if args.mode:
            config_overrides['mode'] = args.mode
        if args.output_dir:
            config_overrides['output_directory'] = args.output_dir
        if args.log_events:
            config_overrides['log_events'] = True

        output_dir = args.output_dir or 'results'
        runner = MultiSessionRunner(
            results_manager=ResultsManager(base_results_dir=output_dir),
            auto_evaluate=not args.no_evaluate,
            parallel_sessions=args.parallel,
            log_judge_prompts=args.log_judge_prompts,
        )

        print(f"Found {len(config_files)} scenario run(s)")
        await runner.run_scenarios(
            config_files,
            config_overrides=config_overrides if config_overrides else None
        )

        runner.results_manager.generate_summary_report()
        if runner.last_batch_id:
            batch_path = runner.results_manager.batches_dir / runner.last_batch_id / "batch_summary.json"
            print(f"Batch summary: {batch_path}")
    else:
        # Single-session path
        config_manager = ConfigManager()
        try:
            config = config_manager.load_config(str(config_files[0]))
            print(f"✅ Loaded configuration from {config_files[0]}")
        except Exception as e:
            print(f"❌ Error loading configuration: {e}")
            sys.exit(1)

        # Apply overrides
        if args.test_name:
            config.test_name = args.test_name
        if args.max_turns:
            config.max_turns = args.max_turns
        if args.mode:
            config.interaction_mode = args.mode
        if args.no_evaluate:
            config.auto_evaluate = False
        if args.log_judge_prompts:
            config.log_judge_prompts = True
        if args.sonic_voice:
            config.sonic_voice_id = args.sonic_voice
        if args.input_mode:
            config.input_mode = args.input_mode
        if args.polly_voice:
            config.polly_voice_id = args.polly_voice
        if args.record_conversation:
            config.record_conversation = True
        if args.output_dir:
            config.output_directory = args.output_dir
        if args.log_events:
            config.log_events = True

        # If config has a dataset_path, route through dataset runner
        if config.dataset_path:
            from runners.multi_session_runner import MultiSessionRunner
            from runners.dataset_loader import DatasetLoader

            loader = DatasetLoader()
            entries = loader.load(config.dataset_path)
            if not entries:
                print("❌ No entries found in dataset")
                sys.exit(1)

            runner = MultiSessionRunner(
                results_manager=ResultsManager(base_results_dir=config.output_directory),
                auto_evaluate=config.auto_evaluate,
                parallel_sessions=args.parallel,
                log_judge_prompts=config.log_judge_prompts,
            )

            await runner.run_dataset(entries=entries, base_config=config)

            runner.results_manager.generate_summary_report()
            if runner.last_batch_id:
                batch_path = runner.results_manager.batches_dir / runner.last_batch_id / "batch_summary.json"
                print(f"Batch summary: {batch_path}")
        else:
            session = LiveInteractionSession(
                config,
                auto_evaluate=config.auto_evaluate,
                evaluation_criteria=config.evaluation_criteria
            )
            await session.run()


if __name__ == "__main__":
    asyncio.run(main())
