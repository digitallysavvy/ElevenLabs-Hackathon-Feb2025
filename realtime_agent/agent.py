import asyncio
import logging
import time
from typing import Any, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor

from agora.rtc.rtc_connection import RTCConnection, RTCConnInfo
from attr import dataclass

from .logger import setup_logger
from .tools import ClientToolCallResponse, ToolContext
from .audio import AgoraAudioInterface
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from .async_conversation import AsyncConversation
from dataclasses import dataclass
from .conversation_config import ConversationConfigOverride, ConversationInitiationData

# Set up the logger with color and timestamp support
logger = setup_logger(name=__name__, log_level=logging.INFO)

@dataclass(frozen=True, kw_only=True)
class InferenceConfig:
    """
    Configuration settings for the ElevenLabs inference.
    
    Attributes:
        system_message: Initial system prompt for the model
        voice: Voice settings for text-to-speech
    """
    system_message: str | None = None
    voice: str | None = None  # Voice ID for ElevenLabs

async def wait_for_remote_user(channel) -> int:
    """
    Wait for a remote user to join the Agora RTC channel.
    
    Args:
        channel: The Agora RTC channel instance
        
    Returns:
        int: The user ID of the first remote user that joins
        
    Raises:
        Exception: If timeout occurs or other errors happen while waiting
    """
    remote_users = list(channel.remote_users.keys())
    if len(remote_users) > 0:
        return remote_users[0]

    future = asyncio.Future[int]()

    channel.once("user_joined", lambda conn, user_id: future.set_result(user_id))

    try:
        # Wait for the remote user with a timeout of 30 seconds
        remote_user = await asyncio.wait_for(future, timeout=15.0)
        return remote_user
    except KeyboardInterrupt:
        future.cancel()
        
    except Exception as e:
        logger.error(f"Error waiting for remote user: {e}")
        raise

@dataclass
class ConversationConfig:
    """Configuration for ElevenLabs conversation"""
    api_key: str
    agent_id: str
    voice_id: str
    model: str
    temperature: float
    stream: bool
    latency_optimization: bool
    conversation_config_override: Optional[Dict[str, Any]] = None
    dynamic_variables: Optional[Dict[str, Any]] = None
    extra_config: Optional[Dict[str, Any]] = None

class ElevenLabsAgent:
    """
    Agent that integrates ElevenLabs' conversational AI with Agora RTC.
    
    This agent:
    - Manages connection between Agora RTC and ElevenLabs' API
    - Handles audio streaming in both directions
    - Manages conversation state and reconnection logic
    - Provides tool integration capabilities
    """
    
    def __init__(
        self,
        channel,
        config: ConversationConfig,
        tools: Optional[List[ToolContext]] = None,
        max_workers: int = 4
    ):
        self.channel = channel
        self.config = config
        self.tools = tools
        self.subscribe_user = None
        self.conversation: Optional[Conversation] = None
        self._thread_pool = ThreadPoolExecutor(max_workers)
        self._start_time = time.time()
        self._disconnected_future = None
        self.audio_interface = None

    async def _setup_agent(self):
        """
        Internal setup for the ElevenLabs agent:
        - Waits for remote user connection
        - Sets up audio interface
        - Configures reconnection handling
        - Initializes ElevenLabs client
        """
        logger.info("Setting up ElevenLabs agent")
        
        # Wait for remote user
        self.subscribe_user = await self.wait_for_remote_user()
        logger.info(f"Subscribing to user {self.subscribe_user}")
        await self.channel.subscribe_audio(self.subscribe_user)
        
        # Create ElevenLabs client
        client = ElevenLabs(api_key=self.config.api_key)
        
        # Create audio interface with the remote user ID
        self.audio_interface = AgoraAudioInterface(
            channel=self.channel,
            remote_uid=self.subscribe_user,  # Pass the remote user ID
            loop=asyncio.get_running_loop()
        )
        
        # Add reconnection handler
        async def handle_connection_error():
            logger.info("Handling WebSocket connection error")
            if self.conversation:
                try:
                    # Wait a moment before reconnecting
                    await asyncio.sleep(1)
                    
                    # Try to gracefully end current session
                    try:
                        await self.conversation.end_session()
                    except Exception as e:
                        logger.warning(f"Error ending session: {e}")
                    
                    # Create a new conversation instance
                    self.conversation = AsyncConversation(
                        Conversation(
                            client=client,
                            agent_id=self.config.agent_id,
                            requires_auth=True,
                            audio_interface=self.audio_interface,  # Use the stored audio interface
                            config=self.conversation_config,
                            client_tools=self.tools if self.tools else None,
                        )
                    )
                    
                    # Start new session
                    await self.conversation.start_session()
                    logger.info("Successfully reconnected to ElevenLabs")
                    
                except Exception as e:
                    logger.error(f"Error reconnecting: {e}")
                    # If reconnection fails, stop the agent
                    await self.stop()
        
        self.audio_interface._on_connection_error = handle_connection_error

    async def wait_for_remote_user(self) -> int:
        """Wait for a remote user to join the channel"""
        remote_users = list(self.channel.remote_users.keys())
        if len(remote_users) > 0:
            return remote_users[0]

        future = asyncio.Future[int]()
        self.channel.once("user_joined", lambda conn, user_id: future.set_result(user_id))

        try:
            return await asyncio.wait_for(future, timeout=15.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Timeout waiting for remote user")

    async def run(self) -> None:
        """
        Main execution loop for the ElevenLabs agent:
        - Sets up the agent and conversation
        - Configures connection state handling
        - Manages the conversation session
        - Handles cleanup on shutdown
        """
        try:
            # Set up the agent
            await self._setup_agent()
            
            # Create conversation configuration
            self.conversation_config = ConversationInitiationData(
                conversation_config_override=ConversationConfigOverride(
                    voice_id=self.config.voice_id,
                    model_id=self.config.model,
                    temperature=self.config.temperature,
                    stream=self.config.stream,
                    latency_optimization=self.config.latency_optimization,
                ).to_dict(),
                dynamic_variables=self.config.dynamic_variables or {},
                extra_body=self.config.extra_config or {}
            )
            
            logger.info(f"Using conversation config: {self.conversation_config}")
            
            # Create conversation with proper config
            client = ElevenLabs(api_key=self.config.api_key)
            self.conversation = AsyncConversation(
                Conversation(
                    client=client,
                    agent_id=self.config.agent_id,
                    requires_auth=True,
                    audio_interface=self.audio_interface,
                    config=self.conversation_config,
                    client_tools=self.tools if self.tools else None,
                )
            )
            
            # Set up disconnect handling
            self._disconnected_future = asyncio.Future()
            
            def on_connection_state_changed(
                agora_rtc_conn: RTCConnection,
                conn_info: RTCConnInfo,
                reason: Any
            ):
                logger.info(f"Connection state changed: {conn_info.state}")
                if conn_info.state == 1 and not self._disconnected_future.done():
                    self._disconnected_future.set_result(None)
                    
            self.channel.on("connection_state_changed", on_connection_state_changed)
            
            # Handle user leaving
            async def on_user_left(agora_rtc_conn: RTCConnection, user_id: int, reason: int):
                logger.info(f"User left: {user_id}")
                if self.subscribe_user == user_id:
                    self.subscribe_user = None
                    logger.info("Subscribed user left, disconnecting")
                    await self.stop()
                    
            self.channel.on("user_left", on_user_left)
            
            # Start the conversation
            await self.conversation.start_session()
            
            # Wait for disconnect
            await self._disconnected_future
            
        except Exception as e:
            logger.error(f"Error running agent: {e}", exc_info=True)
            raise
        finally:
            await self.stop()

    async def stop(self):
        """Stop the agent"""
        try:
            if self.conversation:
                await self.conversation.end_session()
            
            if self.audio_interface:
                self.audio_interface.stop()
                
            if self._thread_pool:
                self._thread_pool.shutdown(wait=True)
                
        except Exception as e:
            logger.error(f"Error stopping agent: {e}", exc_info=True)
