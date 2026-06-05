"""
Nova Sonic Stream Manager for Live Interaction Mode
This module provides a clean interface to amazon.nova-2-sonic-v1:0 without text pre/post processing.
Supports silent audio streaming for text-only interactions.
"""

import os
import asyncio
import base64
import json
import uuid
import warnings
from typing import Optional, Callable, Dict, Any
from rx.subject import Subject
from rx import operators as ops
from rx.scheduler.eventloop import AsyncIOScheduler
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

warnings.filterwarnings("ignore")

# Audio configuration
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
CHUNK_SIZE = 512  # Number of frames per buffer

# Text chunking configuration
TEXT_CHUNK_SIZE = 1000  # Max characters per textInput event


class SonicStreamManager:
    """
    Manages bidirectional streaming with Nova Sonic model.
    Provides text-only interface with silent audio streaming.
    """

    def __init__(
        self,
        model_id: str = 'amazon.nova-2-sonic-v1:0',
        region: str = 'us-east-1',
        endpoint_uri: Optional[str] = None,
        system_prompt: Optional[str] = None,
        voice_id: str = 'matthew',
        inference_config: Optional[Dict[str, Any]] = None,
        tool_config: Optional[Dict[str, Any]] = None,
        tool_handler: Optional[Callable] = None,
        event_callback: Optional[Callable] = None
    ):
        """
        Initialize the Sonic stream manager.

        Args:
            model_id: Model ID for Nova Sonic
            region: AWS region
            endpoint_uri: Optional custom endpoint URI
            system_prompt: System prompt for the assistant
            inference_config: Inference configuration (maxTokens, temperature, topP)
            tool_config: Tool configuration with tools and toolChoice
            tool_handler: Async function to handle tool calls
            event_callback: Callback function for events (text_output, audio_output, tool_use, etc.)
        """
        self.model_id = model_id
        self.region = region
        self.endpoint_uri = endpoint_uri or f"https://bedrock-runtime.{region}.amazonaws.com"
        self.system_prompt = system_prompt or "You are a helpful assistant."
        self.voice_id = voice_id
        self.inference_config = inference_config or {
            "maxTokens": 1024,
            "temperature": 0.7,
            "topP": 0.9
        }
        self.tool_config = tool_config
        if self.tool_config:
            print(f"🛠️  SonicStreamManager received tool_config with {len(self.tool_config.get('tools', []))} tools")
        else:
            print("🛠️  SonicStreamManager received NO tool_config")
        self.tool_handler = tool_handler
        self.event_callback = event_callback

        # Stream components
        self.input_subject = Subject()
        self.output_subject = Subject()
        self.audio_subject = Subject()
        self.bedrock_client = None
        self.stream_response = None
        self.scheduler = None
        self.is_active = False

        # Session information
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())

        # Silent audio task
        self.silent_audio_task = None
        self.audio_content_started = False  # Track if audio content was started

        # Audio pause/resume control (for injecting real audio)
        self._audio_paused = asyncio.Event()
        self._audio_paused.set()  # Start unpaused — silence runs freely

        # Optional callback notified for every input audio chunk sent
        self.input_audio_callback: Optional[Callable] = None

        # Optional event logger for debug mode (JSONL)
        self.event_logger = None

        # Response processing
        self.response_task = None
        self.audio_output_queue = asyncio.Queue()

        # Tool processing
        self.pending_tool_calls = {}
        self.current_tool_use_id = None
        self.current_tool_name = None
        self.current_tool_content = ""

        # Nova Sonic session ID (extracted from incoming events)
        self.sonic_session_id: Optional[str] = None

    def _initialize_client(self):
        """Initialize the Bedrock client."""
        config = Config(
            endpoint_uri=self.endpoint_uri,
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        self.bedrock_client = BedrockRuntimeClient(config=config)


    # Event templates
    START_SESSION_EVENT = '''{
        "event": {
            "sessionStart": {
            "inferenceConfiguration": {
                "maxTokens": 1024,
                "topP": 0.9,
                "temperature": 0.7
                }
            }
        }
    }'''

    def _system_prompt(self, prompt):
        """Create a system prompt"""
        systemPrompt = {
            "event": {
               "textInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.content_name,
                    "content": prompt
                }
            }
        }
    
        return json.dumps(systemPrompt)
    
    def start_prompt(self):
        """Create a promptStart event"""
        
        prompt_start_event = {
            "event": {
                "promptStart": {
                    "promptName": self.prompt_name,
                    "textOutputConfiguration": {
                        "mediaType": "text/plain"
                    },
                    "audioOutputConfiguration": {
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": INPUT_SAMPLE_RATE,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "voiceId": self.voice_id,
                        "encoding": "base64",
                        "audioType": "SPEECH"
                    },
                    "toolUseOutputConfiguration": {
                        "mediaType": "application/json"
                    },
                    "toolConfiguration": {
                        "tools": []
                    }
                }
            }
        }

        if self.tool_config:
            print(f"DEBUG: self.tool_config has {len(self.tool_config.get('tools', []))} tools")

        if self.tool_config and len(self.tool_config.get('tools', [])) > 0:
            # Process tools and convert inputSchema.json to string if needed
            processed_tools = []
            for tool in self.tool_config.get('tools', []):
                processed_tool = self._process_tool(tool)
                processed_tools.append(processed_tool)
            
            prompt_start_event["event"]["promptStart"]["toolConfiguration"]["tools"] = processed_tools
            
            # Add tool_choice if it exists
            if "toolChoice" in self.tool_config:
                prompt_start_event["event"]["promptStart"]["toolConfiguration"]["toolChoice"] = self.tool_config["toolChoice"]
            
            print(f"📋 Tool configuration added to promptStart:")
            print(f"   Number of tools: {len(processed_tools)}")
            for tool in processed_tools:
                print(f"   - {tool.get('toolSpec', {}).get('name', 'Unknown')}")
            

        else:
            prompt_start_event["event"]["promptStart"]["toolConfiguration"] = {"tools": []}
            print(f"⚠️  No tools configured (self.tool_config = {self.tool_config})")
        
        return json.dumps(prompt_start_event)


    def _process_tool(self, tool):
        """Process a tool and ensure inputSchema.json is a string"""
        import copy
        
        # Make a deep copy to avoid modifying the original
        processed_tool = copy.deepcopy(tool)
        
        # Check if it's a toolSpec type
        if 'toolSpec' in processed_tool:
            tool_spec = processed_tool['toolSpec']
            
            # Check if inputSchema.json exists and is a dict (not already a string)
            if 'inputSchema' in tool_spec and 'json' in tool_spec['inputSchema']:
                json_schema = tool_spec['inputSchema']['json']
                
                # If it's a dict, convert to JSON string
                if isinstance(json_schema, dict):
                    tool_spec['inputSchema']['json'] = json.dumps(json_schema)
                    print(f"   🔄 Converted inputSchema.json to string for tool: {tool_spec.get('name', 'Unknown')}")
        
        return processed_tool

    def tool_result_event(self, content_name, content, role):
        """Create a tool result event"""
        if isinstance(content, dict):
            content_json_string = json.dumps(content)
        else:
            content_json_string = content
            
        tool_result_event = {
            "event": {
                "toolResult": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "content": content_json_string
                }
            }
        }
        print(f'Tool result sent: {json.dumps(tool_result_event, indent=2)}')
        return json.dumps(tool_result_event)

    CONTENT_START_EVENT = '''{
        "event": {
            "contentStart": {
            "promptName": "%s",
            "contentName": "%s",
            "type": "AUDIO",
            "interactive": true,
            "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm",
                "sampleRateHertz": 16000,
                "sampleSizeBits": 16,
                "channelCount": 1,
                "audioType": "SPEECH",
                "encoding": "base64"
                }
            }
        }
    }'''

    AUDIO_EVENT_TEMPLATE = '''{
        "event": {
            "audioInput": {
            "promptName": "%s",
            "contentName": "%s",
            "content": "%s"
            }
        }
    }'''

    TEXT_CONTENT_START_EVENT = '''{
        "event": {
            "contentStart": {
            "promptName": "%s",
            "contentName": "%s",
            "role": "%s",
            "type": "TEXT",
            "interactive": false,
                "textInputConfiguration": {
                    "mediaType": "text/plain"
                }
            }
        }
    }'''

    TEXT_CONTENT_START_EVENT_INTERACTIVE = '''{
        "event": {
            "contentStart": {
            "promptName": "%s",
            "contentName": "%s",
            "role": "%s",
            "type": "TEXT",
            "interactive": true,
                "textInputConfiguration": {
                    "mediaType": "text/plain"
                }
            }
        }
    }'''

    TOOL_CONTENT_START_EVENT = '''{
        "event": {
            "contentStart": {
                "promptName": "%s",
                "contentName": "%s",
                "interactive": false,
                "type": "TOOL",
                "role": "TOOL",
                "toolResultInputConfiguration": {
                    "toolUseId": "%s",
                    "type": "TEXT",
                    "textInputConfiguration": {
                        "mediaType": "text/plain"
                    }
                }
            }
        }
    }'''

    TEXT_INPUT_EVENT = '''{
        "event": {
            "textInput": {
            "promptName": "%s",
            "contentName": "%s",
            "content": "%s"
            }
        }
    }'''

    CONTENT_END_EVENT = '''{
        "event": {
            "contentEnd": {
            "promptName": "%s",
            "contentName": "%s"
            }
        }
    }'''

    PROMPT_END_EVENT = '''{
        "event": {
            "promptEnd": {
            "promptName": "%s"
            }
        }
    }'''

    SESSION_END_EVENT = '''{
        "event": {
            "sessionEnd": {}
        }
    }'''

    async def initialize_stream(self) -> 'SonicStreamManager':
        """Initialize the bidirectional stream with Bedrock."""
        if not self.bedrock_client:
            self._initialize_client()

        self.scheduler = AsyncIOScheduler(asyncio.get_event_loop())

        try:
            # Initialize stream
            self.stream_response = await self.bedrock_client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
            )

            self.is_active = True

            # Send initialization events in correct order:
            print("Initialization sequence:")
             # 1. sessionStart
            print(" Sending sessionStart...")
            await self._send_session_start()
            print("sessionStart sent")
            await asyncio.sleep(0.1)

           
            prompt_start = self.start_prompt()

            text_content_start = self.TEXT_CONTENT_START_EVENT % (self.prompt_name, self.content_name, "SYSTEM")
            
            text_content = self._system_prompt(self.system_prompt)
            
            text_content_end = self.CONTENT_END_EVENT % (self.prompt_name, self.content_name)
            
            init_events = [ prompt_start,text_content_start, text_content, text_content_end]
            
            for event in init_events:
                await self._send_raw_event(event)
                # Small delay between init events
                await asyncio.sleep(0.1)
            

            # Start listening for responses BEFORE audio setup
            print("Starting response listener...")
            self.response_task = asyncio.create_task(self._process_responses())
            print("Response listener started")

            # Set up subscription for audio chunks
            print("Setting up audio subscription...")
            self.audio_subject.pipe(
                ops.subscribe_on(self.scheduler)
            ).subscribe(
                on_next=lambda audio_data: asyncio.create_task(self._send_audio_chunk(audio_data)),
                on_error=lambda e: print(f"Audio stream error: {e}")
            )
            print("Audio subscription ready")

            # 5. NOW send audio contentStart
            print("Sending audio contentStart...")
            await self._send_audio_content_start_event()
            self.audio_content_started = True
            print("audio contentStart sent")

            # 6. Start streaming silence audio (required for interactive text inputs)
            print("Starting silence audio stream...")
            self.silent_audio_task = asyncio.create_task(self._send_silent_audio())
            await asyncio.sleep(0.1)
            print("Silence audio streaming started")

            print(f"Sonic stream initialized successfully")
            return self

        except Exception as e:
            self.is_active = False
            print(f"Failed to initialize stream: {str(e)}")
            raise

    async def _send_session_start(self):
        """Send session start event."""
        event = {
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": self.inference_config
                }
            }
        }
        event_json = json.dumps(event)
        await self._send_raw_event(event_json)


    async def _send_system_prompt(self):
        """Send system prompt."""
        content_name = str(uuid.uuid4())

        # Content start
        content_start = {
            "event": {
                "contentStart": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "role": "SYSTEM",
                    "type": "TEXT",
                    "interactive": False,
                    "textInputConfiguration": {
                        "mediaType": "text/plain"
                    }
                }
            }
        }
        await self._send_raw_event(json.dumps(content_start))

        # Text input
        text_input = {
            "event": {
                "textInput": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "content": self.system_prompt
                }
            }
        }
        await self._send_raw_event(json.dumps(text_input))

        # Content end
        content_end = {
            "event": {
                "contentEnd": {
                    "promptName": self.prompt_name,
                    "contentName": content_name
                }
            }
        }
        await self._send_raw_event(json.dumps(content_end))

    async def _send_audio_content_start_event(self):
        """Send a content start event to the Bedrock stream."""
        content_start_event = self.CONTENT_START_EVENT % (self.prompt_name, self.audio_content_name)
        await self._send_raw_event(content_start_event)

    async def _send_silent_audio(self):
        """Send continuous silent audio chunks to maintain connection."""
        silent_chunk_size = CHUNK_SIZE * 2  # 2 bytes per sample for 16-bit (1024 bytes)
        silent_chunk = b'\x00' * silent_chunk_size

        # Calculate proper delay: 512 samples at 16kHz = 32ms of audio
        # Send at 30ms intervals (slightly faster than real-time, matching AWS example)
        delay_seconds = 0.03  # 30ms between chunks

        while self.is_active:
            try:
                # Block while audio is paused (e.g. during Polly playback)
                await self._audio_paused.wait()
                self.audio_subject.on_next(silent_chunk)
                if self.input_audio_callback:
                    self.input_audio_callback(silent_chunk)
                await asyncio.sleep(delay_seconds)
            except Exception as e:
                if self.is_active:
                    print(f"Error sending silent audio: {e}")
                break

    async def send_audio_input(self, pcm_bytes: bytes):
        """
        Send Polly TTS audio to Nova Sonic on the existing audio content,
        replacing silence for the duration.

        Pauses the silence stream, sends PCM chunks directly via
        _send_raw_event on the same audio_content_name, then resumes silence.

        Args:
            pcm_bytes: Raw 16 kHz 16-bit mono PCM bytes.
        """
        chunk_size = CHUNK_SIZE * 2  # 1024 bytes per chunk
        delay_seconds = 0.03  # ~32 ms per chunk at 16 kHz

        self._audio_paused.clear()  # Pause silence
        try:
            # Small drain to let any in-flight silence chunk finish sending
            await asyncio.sleep(0.05)

            for i in range(0, len(pcm_bytes), chunk_size):
                if not self.is_active:
                    break
                chunk = pcm_bytes[i:i + chunk_size]
                # Send directly on the same audio content as silence
                blob = base64.b64encode(chunk).decode('utf-8')
                event = json.dumps({
                    "event": {
                        "audioInput": {
                            "promptName": self.prompt_name,
                            "contentName": self.audio_content_name,
                            "content": blob
                        }
                    }
                })
                await self._send_raw_event(event, log_content=False)
                if self.input_audio_callback:
                    self.input_audio_callback(chunk)
                await asyncio.sleep(delay_seconds)
        finally:
            self._audio_paused.set()  # Resume silence

    async def _send_audio_chunk(self, audio_bytes: bytes):
        """Send audio chunk to the stream."""
        blob = base64.b64encode(audio_bytes)
        event = {
            "event": {
                "audioInput": {
                    "promptName": self.prompt_name,
                    "contentName": self.audio_content_name,
                    "content": blob.decode('utf-8')
                }
            }
        }
        await self._send_raw_event(json.dumps(event))

    async def send_text_message(self, text: str):
        """
        Send a text message to Nova Sonic with a new content name for each turn.

        Large messages are split into multiple textInput events within a single
        contentStart/contentEnd frame:
            contentStart -> textInput(1) -> textInput(2) -> ... -> textInput(N) -> contentEnd

        Args:
            text: The text message to send (will be sent as-is, no preprocessing)
        """
        # Generate NEW content name for this text turn
        text_content_name = str(uuid.uuid4())

        # Escape the text for JSON - use json.dumps to get properly escaped string
        escaped_text = json.dumps(text)[1:-1]  # Remove surrounding quotes from json.dumps result

        # contentStart
        content_start = '''{
            "event": {
                "contentStart": {
                "promptName": "%s",
                "contentName": "%s",
                "role": "%s",
                "type": "TEXT",
                "interactive": true,
                    "textInputConfiguration": {
                        "mediaType": "text/plain"
                    }
                }
            }
        }''' % (self.prompt_name, text_content_name, "USER")

        await self._send_raw_event(content_start)

        # Split escaped_text into chunks and send each as a separate textInput event
        for i in range(0, max(len(escaped_text), 1), TEXT_CHUNK_SIZE):
            chunk = escaped_text[i:i + TEXT_CHUNK_SIZE]
            text_input = '''{
            "event": {
                "textInput": {
                "promptName": "%s",
                "contentName": "%s",
                "content": "%s"
                }
            }
        }''' % (self.prompt_name, text_content_name, chunk)

            await self._send_raw_event(text_input)

        # contentEnd
        content_end = '''{
            "event": {
                "contentEnd": {
                "promptName": "%s",
                "contentName": "%s"
                }
            }
        }''' % (self.prompt_name, text_content_name)

        await self._send_raw_event(content_end)

    async def _send_raw_event(self, event_json: str, log_content: bool = True):
        """Send a raw event JSON to the Bedrock stream."""
        if not self.stream_response or not self.is_active:
            print(f"Cannot send event - stream not active")
            return

        # Log the event JSON (except audio content)
        if log_content:
            try:
                parsed = json.loads(event_json)
                event_type = list(parsed.get('event', {}).keys())[0] if parsed.get('event') else 'unknown'

                # Don't log audioInput events (too verbose)
                if event_type != 'audioInput':
                    print(f"📤 Sending {event_type}:")
                    pretty_json = json.dumps(parsed, indent=2)
                    for line in pretty_json.split('\n'):
                        print(f"   {line}")
            except:
                pass

        # Event logger (JSONL debug mode) — log raw event as-is
        if self.event_logger:
            try:
                parsed_for_log = json.loads(event_json)
                event_type = list(parsed_for_log.get('event', {}).keys())[0] if parsed_for_log.get('event') else 'unknown'
                self.event_logger.log_event('input', event_type, parsed_for_log)
            except:
                pass

        event_bytes = event_json.encode('utf-8')

        event = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_bytes)
        )

        try:
            await self.stream_response.input_stream.send(event)
        except Exception as e:
            print(f"❌ Error sending event: {str(e)}")
            print(f"   Exception type: {type(e).__name__}")
            self.input_subject.on_error(e)

    async def _process_responses(self):
        """Process incoming responses from Bedrock."""
        try:
            while self.is_active:
                try:
                    output = await self.stream_response.await_output()
                    result = await output[1].receive()

                    if result is None:
                        print("📥 DEBUG: Received None result from stream — skipping")
                        continue

                    if not hasattr(result, 'value'):
                        print(f"📥 DEBUG: Result has no .value attribute (type={type(result).__name__}) — skipping")
                        continue

                    if result.value and result.value.bytes_:
                        try:
                            response_data = result.value.bytes_.decode('utf-8')
                            json_data = json.loads(response_data)

                            # Event logger (JSONL debug mode) — log raw event as-is
                            if self.event_logger and 'event' in json_data:
                                try:
                                    evt_type = list(json_data['event'].keys())[0] if json_data['event'] else 'unknown'
                                    self.event_logger.log_event('output', evt_type, json_data)
                                except:
                                    pass

                            # Handle different response types
                            if 'event' in json_data:
                                await self._handle_event(json_data['event'])

                            self.output_subject.on_next(json_data)
                        except json.JSONDecodeError as e:
                            print(f"⚠️  JSON decode error: {e}")
                            print(f"    Raw data: {response_data}")
                            self.output_subject.on_next({"raw_data": response_data})
                    else:
                        print(f"📥 DEBUG: Result has no bytes, result.value={result.value}")

                except StopAsyncIteration:
                    print("📥 DEBUG: StopAsyncIteration - stream ended")
                    break
                except Exception as e:
                    print(f"❌ Error receiving response: {e}")
                    print(f"   Exception type: {type(e).__name__}")
                    print(f"   Exception details: {str(e)}")

                    # Print full exception details
                    import traceback
                    print("   Full traceback:")
                    traceback.print_exc()

                    # Print exception attributes if available
                    if hasattr(e, '__dict__'):
                        print(f"   Exception attributes: {e.__dict__}")

                    # Mark stream as dead so callers don't wait on 120s timeouts
                    self.is_active = False
                    self.output_subject.on_error(e)
                    break

        except Exception as e:
            print(f"❌ Response processing error: {e}")
            print(f"   Exception type: {type(e).__name__}")
            self.is_active = False
            self.output_subject.on_error(e)
        finally:
            if self.is_active:
                self.output_subject.on_completed()

    async def _handle_event(self, event: Dict):
        """Handle incoming events from Sonic."""
        # Extract sessionId from completionStart (fires once per Nova Sonic session)
        if 'completionEnd' in event:
            print("📋 Received completionEnd — no more output events")
            self.is_active = False
            return

        if 'completionStart' in event:
            completion_start = event['completionStart']
            sid = completion_start.get('sessionId')
            if sid:
                self.sonic_session_id = sid
                print(f"📋 Nova Sonic session ID: {sid}")
            if self.event_callback:
                await self.event_callback({
                    'type': 'completion_start',
                    'sonic_session_id': self.sonic_session_id
                })
            return

        if 'contentStart' in event:
            # Track generation stage from contentStart
            content_start = event['contentStart']
            generation_stage = None

            if 'additionalModelFields' in content_start:
                try:
                    additional_fields_str = content_start.get('additionalModelFields', '{}')
                    if isinstance(additional_fields_str, str):
                        additional_fields = json.loads(additional_fields_str)
                    else:
                        additional_fields = additional_fields_str
                    generation_stage = additional_fields.get('generationStage')
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"⚠️  Error parsing additionalModelFields: {e}")
                    generation_stage = None

            if self.event_callback:
                await self.event_callback({
                    'type': 'content_start',
                    'generation_stage': generation_stage,
                    'content_type': content_start.get('type'),
                    'role': content_start.get('role'),
                    'sonic_session_id': self.sonic_session_id
                })

        elif 'textOutput' in event:
            text_content = event['textOutput']['content']
            # Don't print here - let the callback handler decide what to log

            if self.event_callback:
                await self.event_callback({
                    'type': 'text_output',
                    'content': text_content,
                    'sonic_session_id': self.sonic_session_id
                })

        elif 'audioOutput' in event:
            audio_content = event['audioOutput']['content']
            audio_bytes = base64.b64decode(audio_content)
            await self.audio_output_queue.put(audio_bytes)

            if self.event_callback:
                await self.event_callback({
                    'type': 'audio_output',
                    'audio_bytes': audio_bytes,
                    'sonic_session_id': self.sonic_session_id
                })

        elif 'toolUse' in event:
            self.current_tool_use_id = event['toolUse'].get('toolUseId')
            self.current_tool_name = event['toolUse'].get('toolName')
            self.current_tool_content = event['toolUse']
            print(f"🔧 Tool call: {event}")

            if self.event_callback:
                await self.event_callback({
                    'type': 'tool_use',
                    'tool_name': self.current_tool_name,
                    'tool_use_id': self.current_tool_use_id,
                    'content': self.current_tool_content,
                    'sonic_session_id': self.sonic_session_id
                })

        elif 'contentEnd' in event:
            content_end = event['contentEnd']
            stop_reason = content_end.get('stopReason')
            content_type = content_end.get('type')

            # Notify callback about content end
            if self.event_callback:
                await self.event_callback({
                    'type': 'content_end',
                    'stop_reason': stop_reason,
                    'content_type': content_type,
                    'sonic_session_id': self.sonic_session_id
                })

            # Handle tool requests
            if content_type == 'TOOL' and self.tool_handler:
                # Tool use content ended - process tool
                await self._handle_tool_request()

    async def _handle_tool_request(self):
        """Handle a tool request asynchronously."""
        if not self.tool_handler or not self.current_tool_name:
            return

        tool_content_name = str(uuid.uuid4())

        try:
            print(f"⚙️  Executing tool: {self.current_tool_name}")

            # Execute tool
            tool_result = await self.tool_handler(
                self.current_tool_name,
                self.current_tool_content,
                self.current_tool_use_id
            )

            # Send tool result
            await self._send_tool_result(tool_content_name, self.current_tool_use_id, tool_result)

            print(f"Tool execution complete: {self.current_tool_name}")

        except Exception as e:
            print(f"❌ Error executing tool {self.current_tool_name}: {str(e)}")
            error_result = {
                "status": "error",
                "error": f"Tool execution failed: {str(e)}"
            }
            await self._send_tool_result(tool_content_name, self.current_tool_use_id, error_result)

    async def _send_tool_result(self, content_name: str, tool_use_id: str, result: Dict):
        """Send tool result back to Sonic."""
        
        content_start_event = self.TOOL_CONTENT_START_EVENT % (self.prompt_name, content_name, tool_use_id)
        await self._send_raw_event(content_start_event)
        
        # Tool result event
        tool_result = {
            "event": {
                "toolResult": {
                    "promptName": self.prompt_name,
                    "contentName": content_name,
                    "content": json.dumps(result) if isinstance(result, dict) else result
                }
            }
        }
        await self._send_raw_event(json.dumps(tool_result))

        content_end_event = self.CONTENT_END_EVENT % (self.prompt_name, content_name)
        await self._send_raw_event(content_end_event)


    async def close(self):
        """Close the stream properly.

        Sequence: stop audio → send end events → wait for completionEnd
        (which sets is_active=False) → close input stream → clean up.

        The entire close operation is wrapped in a hard timeout to prevent
        hanging on broken streams (e.g. after Ctrl+C or stream errors).
        """
        print("Closing stream...")

        try:
            await asyncio.wait_for(self._close_inner(), timeout=10.0)
        except asyncio.TimeoutError:
            print("⚠️  Close timed out after 10s — forcing shutdown")
            self.is_active = False
        except Exception as e:
            print(f"⚠️  Error during close: {e}")
            self.is_active = False
        finally:
            # Always cancel lingering tasks and complete subjects
            self._force_cleanup()

        print("✅ Stream closed")

    async def _close_inner(self):
        """Inner close logic — called under a timeout by close()."""
        # 1. Stop producing silent audio
        if self.silent_audio_task and not self.silent_audio_task.done():
            self.silent_audio_task.cancel()
            try:
                await self.silent_audio_task
            except asyncio.CancelledError:
                pass

        # Brief pause to let in-flight audio sends complete
        await asyncio.sleep(0.1)

        # 2. Send end events only if stream is still active
        if self.is_active:
            try:
                if self.audio_content_started:
                    content_end = {
                        "event": {
                            "contentEnd": {
                                "promptName": self.prompt_name,
                                "contentName": self.audio_content_name
                            }
                        }
                    }
                    await self._send_raw_event(json.dumps(content_end))

                prompt_end = {
                    "event": {
                        "promptEnd": {
                            "promptName": self.prompt_name
                        }
                    }
                }
                await self._send_raw_event(json.dumps(prompt_end))

                session_end = {
                    "event": {
                        "sessionEnd": {}
                    }
                }
                await self._send_raw_event(json.dumps(session_end))
            except Exception as e:
                print(f"Error sending end events: {e}")

        # 3. Wait for completionEnd to set is_active=False (with timeout)
        try:
            deadline = 5.0
            waited = 0.0
            while self.is_active and waited < deadline:
                await asyncio.sleep(0.1)
                waited += 0.1
            if self.is_active:
                print("⚠️  completionEnd not received within timeout, forcing close")
                self.is_active = False
        except Exception:
            self.is_active = False

        # 4. Close input stream
        if self.stream_response:
            try:
                await self.stream_response.input_stream.close()
            except Exception as e:
                print(f"Error closing input stream: {e}")

        # 5. Wait for response task to finish naturally
        if self.response_task and not self.response_task.done():
            try:
                await asyncio.wait_for(self.response_task, timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                self.response_task.cancel()
                try:
                    await self.response_task
                except asyncio.CancelledError:
                    pass

    def _force_cleanup(self):
        """Force-cancel any lingering tasks and complete RxPY subjects.
        Safe to call multiple times.
        """
        self.is_active = False

        # Cancel silent audio if still running
        if self.silent_audio_task and not self.silent_audio_task.done():
            self.silent_audio_task.cancel()

        # Cancel response task if still running
        if self.response_task and not self.response_task.done():
            self.response_task.cancel()

        # Complete RxPY subjects
        try:
            self.input_subject.on_completed()
            self.audio_subject.on_completed()
        except Exception:
            pass


async def main():
    """Test the SonicStreamManager with a simple text message."""
    print("Testing SonicStreamManager")

    # Create stream manager
    from model_registry import get_model_registry
    registry = get_model_registry()
    sonic = registry.resolve_sonic_model("nova-sonic")
    manager = SonicStreamManager(
        model_id=sonic["model_id"],
        region=sonic["region"],
        system_prompt="You are a helpful AI assistant. Be concise and friendly.",
        inference_config={
            "maxTokens": 1024,
            "temperature": 0.7,
            "topP": 0.9
        }
    )

    try:
        # Initialize stream
        await manager.initialize_stream()

        # Send a test message
        print("\n" + "="*60)
        print("Sending test message: 'Hello, how are you?'")
        print("="*60 + "\n")

        await manager.send_text_message("Hey! I'm curious - what's something you've learned recently that surprised you?")

        # Wait for response
        print("\nWaiting 10 seconds for response...")
        await asyncio.sleep(10)

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Close stream
        await manager.close()
        print("\n🧪 Test complete")


if __name__ == "__main__":
    asyncio.run(main())
