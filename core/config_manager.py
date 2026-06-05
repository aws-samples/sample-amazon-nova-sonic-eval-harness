"""
Configuration Manager for Live Interaction Mode
Handles loading and managing configurations for tests.
"""

import json
import yaml
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict


@dataclass
class InferenceConfig:
    """Inference configuration for Nova Sonic."""
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.9


@dataclass
class ToolConfig:
    """Tool configuration."""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    tool_choice: Dict[str, Any] = field(default_factory=lambda: {"auto": {}})


@dataclass
class TestConfig:
    """Configuration for a single test."""
    test_name: str
    description: str = ""

    # Nova Sonic configuration
    sonic_model_id: str = "nova-sonic"
    sonic_region: str = "us-east-1"
    sonic_endpoint_uri: Optional[str] = None
    sonic_system_prompt: str = "You are a helpful assistant."
    sonic_voice_id: str = "matthew"  # Nova Sonic output voice (e.g. matthew, tiffany, amy)
    sonic_inference_config: InferenceConfig = field(default_factory=InferenceConfig)
    sonic_tool_config: Optional[ToolConfig] = None

    # User simulator configuration
    user_model_id: str = "claude-haiku"
    user_system_prompt: Optional[str] = None
    user_max_tokens: int = 1024
    user_temperature: float = 1.0

    # Interaction configuration
    interaction_mode: str = "bedrock"  # "bedrock" or "scripted"
    scripted_messages: List[str] = field(default_factory=list)
    max_turns: int = 10
    turn_delay: float = 0  # seconds between turns (increase if throttled)

    # Logging configuration
    log_audio: bool = True
    log_text: bool = True
    log_tools: bool = True
    log_events: bool = True
    output_directory: str = "results"

    # Evaluation configuration
    auto_evaluate: bool = False
    evaluation_criteria: Optional[Dict[str, Any]] = None
    log_judge_prompts: bool = False

    # Tool registry configuration
    tool_registry_module: Optional[str] = None  # e.g., "examples.example_with_tool_registry"

    # Audio input mode
    input_mode: str = "text"              # "text" (default) or "polly"
    polly_voice_id: str = "Matthew"
    polly_engine: str = "neural"          # "neural" or "standard"
    polly_region: Optional[str] = None    # defaults to sonic_region

    # Full conversation recording
    record_conversation: bool = False

    # Dataset configuration
    dataset_path: Optional[str] = None  # Path to JSONL dataset or hf://org/dataset

    # Hangup prompt — auto-appends [HANGUP] instruction to user_system_prompt
    hangup_prompt_enabled: bool = True

    # Session continuation
    enable_session_continuation: bool = True
    transition_threshold_seconds: float = 360.0
    audio_buffer_duration_seconds: float = 3.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TestConfig':
        """Create from dictionary."""
        # Migrate legacy claude_* keys to user_* keys
        _legacy_map = {
            'claude_model': 'user_model_id',
            'claude_system_prompt': 'user_system_prompt',
            'claude_max_tokens': 'user_max_tokens',
            'claude_temperature': 'user_temperature',
        }
        for old_key, new_key in _legacy_map.items():
            if old_key in data and new_key not in data:
                data[new_key] = data.pop(old_key)
            elif old_key in data:
                data.pop(old_key)

        # Handle nested objects
        if 'sonic_inference_config' in data:
            if isinstance(data['sonic_inference_config'], dict):
                data['sonic_inference_config'] = InferenceConfig(**data['sonic_inference_config'])

        if 'sonic_tool_config' in data and data['sonic_tool_config']:
            if isinstance(data['sonic_tool_config'], dict):
                tc = data['sonic_tool_config']
                # Accept camelCase toolChoice (Nova Sonic API format) as tool_choice
                if 'toolChoice' in tc and 'tool_choice' not in tc:
                    tc['tool_choice'] = tc.pop('toolChoice')
                data['sonic_tool_config'] = ToolConfig(**tc)

        # Remove deprecated keys that are no longer fields on TestConfig
        data.pop('parallel_sessions', None)

        return cls(**data)


class ConfigManager:
    """Manages test configurations."""

    def __init__(self, config_dir: str = "configs"):
        """
        Initialize config manager.

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self, config_path: str) -> TestConfig:
        """
        Load configuration from file.

        Args:
            config_path: Path to configuration file (JSON or YAML)

        Returns:
            TestConfig object
        """
        path = Path(config_path)

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load based on file extension
        if path.suffix in ['.yaml', '.yml']:
            with open(path, 'r') as f:
                data = yaml.safe_load(f)
        elif path.suffix == '.json':
            with open(path, 'r') as f:
                data = json.load(f)
        else:
            raise ValueError(f"Unsupported configuration file format: {path.suffix}")

        # Override with environment variables if present
        data = self._apply_env_overrides(data)

        return TestConfig.from_dict(data)

    def _apply_env_overrides(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply environment variable overrides to config data.

        Args:
            data: Config data dictionary

        Returns:
            Updated config data
        """
        # Override sonic settings from env vars
        if os.getenv('SONIC_MODEL_ID'):
            data['sonic_model_id'] = os.getenv('SONIC_MODEL_ID')
        if os.getenv('SONIC_REGION'):
            data['sonic_region'] = os.getenv('SONIC_REGION')
        if os.getenv('SONIC_ENDPOINT_URI'):
            data['sonic_endpoint_uri'] = os.getenv('SONIC_ENDPOINT_URI')

        return data

    def save_config(self, config: TestConfig, config_path: str, format: str = "json"):
        """
        Save configuration to file.

        Args:
            config: TestConfig object
            config_path: Path to save configuration
            format: "json" or "yaml"
        """
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = config.to_dict()

        if format == "yaml":
            with open(path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        else:  # json
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)

        print(f"✅ Configuration saved to {path}")

    def list_configs(self) -> List[str]:
        """
        List all configuration files in the config directory.

        Returns:
            List of configuration file paths
        """
        configs = []
        for ext in ['.json', '.yaml', '.yml']:
            configs.extend([str(p) for p in self.config_dir.glob(f"*{ext}")])
        return configs

    def create_default_config(self, test_name: str, save_path: Optional[str] = None) -> TestConfig:
        """
        Create a default configuration.

        Args:
            test_name: Name for the test
            save_path: Optional path to save the config

        Returns:
            TestConfig object
        """
        config = TestConfig(
            test_name=test_name,
            description=f"Default configuration for {test_name}",
            sonic_system_prompt=self._get_default_sonic_prompt(),
            user_system_prompt=self._get_default_user_prompt()
        )

        if save_path:
            self.save_config(config, save_path)

        return config

    def _get_default_sonic_prompt(self) -> str:
        """Get default Nova Sonic system prompt."""
        return """You are a helpful AI assistant. Your role is to assist users with their questions and tasks.

Guidelines:
- Be helpful, accurate, and concise
- Use tools when appropriate to provide accurate information
- Maintain a professional yet friendly tone
- Ask clarifying questions when needed"""

    def _get_default_user_prompt(self) -> str:
        """Get default system prompt for the simulated user."""
        return """You are simulating a user having a natural conversation with an AI assistant.

Your role:
- Generate realistic, natural user queries and responses
- React naturally to the assistant's responses
- Ask follow-up questions when appropriate
- Keep your messages conversational and human-like (1-3 sentences typically)

IMPORTANT: Only output the user's message. Do not include any meta-commentary."""


def load_tools_from_file(tools_file: str) -> List[Dict[str, Any]]:
    """
    Load tool definitions from a file.

    Args:
        tools_file: Path to tools JSON/YAML file

    Returns:
        List of tool definitions
    """
    path = Path(tools_file)

    if not path.exists():
        raise FileNotFoundError(f"Tools file not found: {tools_file}")

    if path.suffix in ['.yaml', '.yml']:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
    else:  # json
        with open(path, 'r') as f:
            data = json.load(f)

    # Handle both direct list and nested structure
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and 'tools' in data:
        return data['tools']
    else:
        raise ValueError("Invalid tools file format. Expected list of tools or dict with 'tools' key.")


def create_tool_config(tools: List[Dict[str, Any]], tool_choice: Optional[Dict[str, Any]] = None) -> ToolConfig:
    """
    Create a ToolConfig from tool definitions.

    Args:
        tools: List of tool definitions
        tool_choice: Optional tool choice configuration

    Returns:
        ToolConfig object
    """
    return ToolConfig(
        tools=tools,
        tool_choice=tool_choice or {"auto": {}}
    )
