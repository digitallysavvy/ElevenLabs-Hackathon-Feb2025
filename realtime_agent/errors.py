from typing import Optional
from dataclasses import dataclass

@dataclass
class ElevenLabsError(Exception):
    """Base class for ElevenLabs errors"""
    message: str
    code: Optional[str] = None
    details: Optional[dict] = None

@dataclass
class ConfigurationError(ElevenLabsError):
    """Error in configuration"""
    pass

@dataclass
class ConnectionError(ElevenLabsError):
    """Error in WebSocket connection"""
    pass

@dataclass
class AudioProcessingError(ElevenLabsError):
    """Error in audio processing"""
    pass

@dataclass
class ConversationError(ElevenLabsError):
    """Error in conversation handling"""
    pass

def handle_error(error: Exception) -> ElevenLabsError:
    """Convert exceptions to ElevenLabsError types"""
    if isinstance(error, ElevenLabsError):
        return error
        
    # Map error types
    if isinstance(error, ValueError):
        return ConfigurationError(str(error))
    elif isinstance(error, ConnectionError):
        return ConnectionError(str(error))
    elif isinstance(error, (OSError, IOError)):
        return AudioProcessingError(str(error))
    
    # Default error
    return ElevenLabsError(str(error)) 