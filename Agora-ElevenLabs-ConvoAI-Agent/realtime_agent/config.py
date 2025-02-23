import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv
import json
import logging
from .logger import setup_logger

# Set up a basic logger
logger = setup_logger(name=__name__, log_level=logging.INFO)

@dataclass
class ElevenLabsConfig:
    """Configuration for ElevenLabs agent"""
    api_key: str
    agent_id: str
    model: Optional[str] = None
    voice_id: Optional[str] = None
    temperature: float = 0.8
    dynamic_variables: Optional[Dict[str, Any]] = None
    extra_config: Optional[Dict[str, Any]] = None
    latency_optimization: bool = True
    input_sampling_rate: int = 16000
    output_sampling_rate: int = 24000
    max_response_tokens: Optional[int] = None
    debug_logging: bool = False
    stream: bool = True  # Enable streaming by default
    
    @classmethod
    def from_env(cls) -> "ElevenLabsConfig":
        """Create config from environment variables"""
        load_dotenv()
        
        # Required fields
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY must be set in environment or .env file")
            
        agent_id = os.getenv("ELEVENLABS_AGENT_ID")
        if not agent_id:
            raise ValueError("ELEVENLABS_AGENT_ID must be set in environment or .env file")
            
        return cls(
            api_key=api_key,
            agent_id=agent_id,
            model=os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2"),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "alloy"),
            debug_logging=os.getenv("DEBUG_LOGGING", "false").lower() == "true",
            latency_optimization=os.getenv("LATENCY_OPTIMIZATION", "true").lower() == "true",
            stream=os.getenv("STREAM", "true").lower() == "true",
        )
    
    def validate(self) -> None:
        """Validate the configuration"""
        if not self.api_key:
            raise ValueError("API key is required")
        if not self.agent_id:
            raise ValueError("Agent ID is required")
            
        if self.temperature is not None and not 0 <= self.temperature <= 1:
            raise ValueError("Temperature must be between 0 and 1")
            
        if self.max_response_tokens is not None and self.max_response_tokens <= 0:
            raise ValueError("Max response tokens must be positive")
            