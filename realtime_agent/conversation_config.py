from dataclasses import dataclass, field
from typing import Optional, Dict, Any

@dataclass
class ConversationConfigOverride:
    """Configuration overrides for the conversation"""
    voice_id: Optional[str] = None
    model_id: Optional[str] = None
    temperature: float = 0.8
    stream: bool = True
    latency_optimization: bool = True
    max_response_tokens: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        return {k: v for k, v in self.__dict__.items() if v is not None}

@dataclass
class ConversationInitiationData:
    """Complete configuration for ElevenLabs conversation"""
    extra_body: Dict[str, Any] = field(default_factory=dict)
    conversation_config_override: Dict[str, Any] = field(default_factory=dict)
    dynamic_variables: Dict[str, Any] = field(default_factory=dict) 