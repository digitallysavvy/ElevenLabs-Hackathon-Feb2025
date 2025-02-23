import asyncio
from typing import Optional
from elevenlabs.conversational_ai.conversation import Conversation
import logging
from .logger import setup_logger

logger = setup_logger(name=__name__, log_level=logging.INFO)

class AsyncConversation:
    """Async wrapper around ElevenLabs Conversation class"""
    
    def __init__(self, conversation: Conversation):
        self._conversation = conversation
        
    async def start_session(self):
        """Start conversation session asynchronously"""
        try:
            await asyncio.to_thread(self._conversation.start_session)
        except Exception as e:
            logger.error(f"Error starting conversation session: {e}")
            raise
    
    async def end_session(self):
        """End conversation session asynchronously"""
        if self._conversation:
            try:
                await asyncio.to_thread(self._conversation.end_session)
            except Exception as e:
                logger.error(f"Error ending conversation session: {e}")
                
    async def wait_for_session_end(self) -> Optional[str]:
        """Wait for conversation to end asynchronously"""
        try:
            return await asyncio.to_thread(self._conversation.wait_for_session_end)
        except Exception as e:
            logger.error(f"Error waiting for session end: {e}")
            raise 