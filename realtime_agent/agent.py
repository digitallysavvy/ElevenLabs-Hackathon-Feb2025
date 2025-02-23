import asyncio
import base64
import logging
import os
from builtins import anext
from typing import Any, Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time

from agora.rtc.rtc_connection import RTCConnection, RTCConnInfo
from attr import dataclass

from agora_realtime_ai_api.rtc import Channel, ChatMessage, RtcEngine, RtcOptions

from .logger import setup_logger
from .realtime.struct import (
    ErrorMessage, FunctionCallOutputItemParam, InputAudioBufferCommitted, 
    InputAudioBufferSpeechStarted, InputAudioBufferSpeechStopped, InputAudioTranscription, 
    ItemCreate, ItemCreated, ItemInputAudioTranscriptionCompleted, RateLimitsUpdated, 
    ResponseAudioDelta, ResponseAudioDone, ResponseAudioTranscriptDelta, 
    ResponseAudioTranscriptDone, ResponseContentPartAdded, ResponseContentPartDone, 
    ResponseCreate, ResponseCreated, ResponseDone, ResponseFunctionCallArgumentsDelta, 
    ResponseFunctionCallArgumentsDone, ResponseOutputItemAdded, ResponseOutputItemDone, 
    ServerVADUpdateParams, SessionUpdate, SessionUpdateParams, SessionUpdated, 
    Voices, to_json
)
from .realtime.connection import RealtimeApiConnection
from .tools import ClientToolCallResponse, ToolContext
from .utils import PCMWriter
from .audio import AgoraAudioInterface
from .config import ElevenLabsConfig
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ConversationConfig
from .errors import handle_error, ElevenLabsError
from .async_conversation import AsyncConversation
from dataclasses import dataclass
from types import SimpleNamespace
from .conversation_config import ConversationConfigOverride, ConversationInitiationData

# Set up the logger with color and timestamp support
logger = setup_logger(name=__name__, log_level=logging.INFO)

def _monitor_queue_size(queue: asyncio.Queue, queue_name: str, threshold: int = 5) -> None:
    queue_size = queue.qsize()
    if queue_size > threshold:
        logger.warning(f"Queue {queue_name} size exceeded {threshold}: current size {queue_size}")


async def wait_for_remote_user(channel: Channel) -> int:
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


@dataclass(frozen=True, kw_only=True)
class InferenceConfig:
    """
    Configuration settings for the real-time inference.
    
    Attributes:
        system_message: Initial system prompt for the model
        turn_detection: Parameters for voice activity detection
        voice: Voice settings for text-to-speech
    """
    system_message: str | None = None
    turn_detection: ServerVADUpdateParams | None = None  # MARK: CHECK!
    voice: Voices | None = None


class RealtimeKitAgent:
    """
    Agent that handles real-time audio communication between Agora RTC and OpenAI's real-time API.
    
    This agent:
    - Processes audio from Agora RTC and sends it to OpenAI
    - Receives responses from OpenAI and plays them through Agora RTC
    - Handles function calls and tool usage
    - Manages the bi-directional audio stream
    """
    engine: RtcEngine
    channel: Channel
    connection: RealtimeApiConnection
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue()

    message_queue: asyncio.Queue[ResponseAudioTranscriptDelta] = (
        asyncio.Queue()
    )
    message_done_queue: asyncio.Queue[ResponseAudioTranscriptDone] = (
        asyncio.Queue()
    )
    tools: ToolContext | None = None

    _client_tool_futures: dict[str, asyncio.Future[ClientToolCallResponse]]

    @classmethod
    async def setup_and_run_agent(
        cls,
        *,
        engine: RtcEngine,
        options: RtcOptions,
        inference_config: InferenceConfig,
        tools: ToolContext | None,
    ) -> None:
        """
        Set up and run the real-time agent with the specified configuration.
        
        Args:
            engine: The Agora RTC engine instance
            options: RTC connection options
            inference_config: Configuration for the inference model
            tools: Optional tools/functions available to the model
        """
        channel = engine.create_channel(options)
        await channel.connect()

        try:
            async with RealtimeApiConnection(
                base_uri=os.getenv("REALTIME_API_BASE_URI", "wss://api.openai.com"),
                api_key=os.getenv("OPENAI_API_KEY"),
                verbose=False,
            ) as connection:
                await connection.send_request(
                    SessionUpdate(
                        session=SessionUpdateParams(
                            # MARK: check this
                            turn_detection=inference_config.turn_detection,
                            tools=tools.model_description() if tools else [],
                            tool_choice="auto",
                            input_audio_format="pcm16",
                            output_audio_format="pcm16",
                            instructions=inference_config.system_message,
                            voice=inference_config.voice,
                            model=os.environ.get("OPENAI_MODEL", "gpt-4o-realtime-preview"),
                            modalities=["text", "audio"],
                            temperature=0.8,
                            max_response_output_tokens="inf",
                            input_audio_transcription=InputAudioTranscription(model="whisper-1")
                        )
                    )
                )

                start_session_message = await anext(connection.listen())
                # assert isinstance(start_session_message, messages.StartSession)
                if isinstance(start_session_message, SessionUpdated):
                    logger.info(
                        f"Session started: {start_session_message.session.id} model: {start_session_message.session.model}"
                    )
                elif isinstance(start_session_message, ErrorMessage):
                    logger.info(
                        f"Error: {start_session_message.error}"
                    )

                agent = cls(
                    connection=connection,
                    tools=tools,
                    channel=channel,
                )
                await agent.run()

        finally:
            await channel.disconnect()
            await connection.close()

    def __init__(
        self,
        *,
        connection: RealtimeApiConnection,
        tools: ToolContext | None,
        channel: Channel,
    ) -> None:
        self.connection = connection
        self.tools = tools
        self._client_tool_futures = {}
        self.channel = channel
        self.subscribe_user = None
        self.write_pcm = os.environ.get("WRITE_AGENT_PCM", "false") == "true"
        logger.info(f"Write PCM: {self.write_pcm}")

        # Add audio send queue
        self._audio_send_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.is_running = True

        # Create audio interface with input callback
        self.audio_interface = AgoraAudioInterface(
            channel=self.channel, 
            loop=asyncio.get_running_loop()
        )

    async def _process_audio_send_queue(self):
        """
        Process audio chunks from Agora RTC and send them to OpenAI's API.
        Runs continuously in the background, taking audio chunks from the queue
        and sending them as base64-encoded data.
        """
        while self.is_running:
            try:
                audio_chunk = await self._audio_send_queue.get()
                await self.connection.send_request({
                    "type": "input_audio_data",
                    "audio": base64.b64encode(audio_chunk).decode()
                })
            except Exception as e:
                logger.error(f"Error processing audio chunk: {e}")
                await asyncio.sleep(0.1)

    async def run(self) -> None:
        """
        Main loop for the agent. Handles:
        - Waiting for remote user connection
        - Setting up audio processing
        - Managing audio subscriptions
        - Processing model messages
        - Cleanup on shutdown
        """
        try:
            def log_exception(t: asyncio.Task[Any]) -> None:
                if not t.cancelled() and t.exception():
                    logger.error(
                        "unhandled exception",
                        exc_info=t.exception(),
                    )

            logger.info("Waiting for remote user to join")
            self.subscribe_user = await wait_for_remote_user(self.channel)
            logger.info(f"Subscribing to user {self.subscribe_user}")
            
            # Start the audio send queue processor only if not using ElevenLabs integration.
            if not getattr(self, 'use_elevenlabs', False):
                audio_processor_task = asyncio.create_task(
                    self._process_audio_send_queue()
                )
                audio_processor_task.add_done_callback(log_exception)
            
            # Start the audio interface.
            # Use a different input_callback if using ElevenLabs.
            if getattr(self, 'use_elevenlabs', False):
                # In ElevenLabsAgent, the conversation (in conversation.py) calls
                # self.audio_interface.start(input_callback) itself.
                logger.info("Skipping audio_interface.start() in RealtimeKitAgent (ElevenLabs mode)")
            else:
                self.audio_interface.start(
                    input_callback=lambda audio: self._audio_send_queue.put_nowait(audio)
                )
            
            # Subscribe to audio
            await self.channel.subscribe_audio(self.subscribe_user)
            logger.info(f"Successfully subscribed to audio for user {self.subscribe_user}")

            # Verify subscription worked
            if not self.channel.is_subscribed_to_audio(self.subscribe_user):
                logger.error(f"Failed to subscribe to audio for user {self.subscribe_user}")

            disconnected_future = asyncio.Future[None]()

            def callback(agora_rtc_conn: RTCConnection, conn_info: RTCConnInfo, reason):
                logger.info(f"Connection state changed: {conn_info.state}")
                if conn_info.state == 1:
                    if not disconnected_future.done():
                        disconnected_future.set_result(None)

            self.channel.on("connection_state_changed", callback)

            # All rtc to model audio is handled by AgoraAudioInterface
            logger.info("Starting _process_model_messages task")
            message_processor_task = asyncio.create_task(
                self._process_model_messages()
            )
            message_processor_task.add_done_callback(log_exception)

            await disconnected_future
            logger.info("Agent finished running")
            
        except asyncio.CancelledError:
            logger.info("Agent cancelled")
        except Exception as e:
            logger.error(f"Error running agent: {e}")
            raise
        finally:
            # Clean up
            self.is_running = False
            # Make sure to stop the audio interface
            self.audio_interface.stop()

    async def model_to_rtc(self) -> None:
        """
        Handle audio output from the model to RTC.
        Takes audio frames from the queue and sends them through the audio interface.
        Also handles PCM writing for debugging if enabled.
        """
        pcm_writer = PCMWriter(prefix="model_to_rtc", write_pcm=self.write_pcm)

        try:
            while True:
                frame = await self.audio_queue.get()
                # Send audio through the audio interface instead of directly
                self.audio_interface.output(frame)
                await pcm_writer.write(frame)

        except asyncio.CancelledError:
            await pcm_writer.flush()
            raise

    async def handle_funtion_call(self, message: ResponseFunctionCallArgumentsDone) -> None:
        """
        Execute tool/function calls requested by the model and send back the results.
        
        Args:
            message: Contains function name and arguments from the model
        """
        function_call_response = await self.tools.execute_tool(message.name, message.arguments)
        logger.info(f"Function call response: {function_call_response}")
        await self.connection.send_request(
            ItemCreate(
                item = FunctionCallOutputItemParam(
                    call_id=message.call_id,
                    output=function_call_response.json_encoded_output
                )
            )
        )
        await self.connection.send_request(
            ResponseCreate()
        )

    async def _process_model_messages(self) -> None:
        """
        Process all incoming messages from the model.
        Handles various message types including:
        - Audio deltas (speech output)
        - Transcripts
        - Speech detection events
        - Function calls
        - Various status updates
        """
        async for message in self.connection.listen():
            # logger.info(f"Received message {message=}")
            match message:
                case ResponseAudioDelta():
                    # logger.info("Received audio message")
                    self.audio_queue.put_nowait(base64.b64decode(message.delta))
                    # loop.call_soon_threadsafe(self.audio_queue.put_nowait, base64.b64decode(message.delta))
                    logger.debug(f"TMS:ResponseAudioDelta: response_id:{message.response_id},item_id: {message.item_id}")
                case ResponseAudioTranscriptDelta():
                    # logger.info(f"Received text message {message=}")
                    asyncio.create_task(self.channel.chat.send_message(
                        ChatMessage(
                            message=to_json(message), msg_id=message.item_id
                        )
                    ))

                case ResponseAudioTranscriptDone():
                    logger.info(f"Text message done: {message=}")
                    asyncio.create_task(self.channel.chat.send_message(
                        ChatMessage(
                            message=to_json(message), msg_id=message.item_id
                        )
                    ))
                case InputAudioBufferSpeechStarted():
                    await self.channel.clear_sender_audio_buffer()
                    # clear the audio queue so audio stops playing
                    while not self.audio_queue.empty():
                        self.audio_queue.get_nowait()
                    logger.info(f"TMS:InputAudioBufferSpeechStarted: item_id: {message.item_id}")
                case InputAudioBufferSpeechStopped():
                    logger.info(f"TMS:InputAudioBufferSpeechStopped: item_id: {message.item_id}")
                    pass
                case ItemInputAudioTranscriptionCompleted():
                    logger.info(f"ItemInputAudioTranscriptionCompleted: {message=}")
                    asyncio.create_task(self.channel.chat.send_message(
                        ChatMessage(
                            message=to_json(message), msg_id=message.item_id
                        )
                    ))
                #  InputAudioBufferCommitted
                case InputAudioBufferCommitted():
                    pass
                case ItemCreated():
                    pass
                # ResponseCreated
                case ResponseCreated():
                    pass
                # ResponseDone
                case ResponseDone():
                    pass

                # ResponseOutputItemAdded
                case ResponseOutputItemAdded():
                    pass

                # ResponseContenPartAdded
                case ResponseContentPartAdded():
                    pass
                # ResponseAudioDone
                case ResponseAudioDone():
                    pass
                # ResponseContentPartDone
                case ResponseContentPartDone():
                    pass
                # ResponseOutputItemDone
                case ResponseOutputItemDone():
                    pass
                case SessionUpdated():
                    pass
                case RateLimitsUpdated():
                    pass
                case ResponseFunctionCallArgumentsDone():
                    asyncio.create_task(
                        self.handle_funtion_call(message)
                    )
                case ResponseFunctionCallArgumentsDelta():
                    pass

                case _:
                    logger.warning(f"Unhandled message {message=}")


@dataclass
class ConversationConfig:
    """Configuration for ElevenLabs conversation"""
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
