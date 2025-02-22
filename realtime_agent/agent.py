import asyncio
import base64
import logging
import os
from builtins import anext
from typing import Any, Optional, Dict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time

from agora.rtc.rtc_connection import RTCConnection, RTCConnInfo
from attr import dataclass

from agora_realtime_ai_api.rtc import Channel, ChatMessage, RtcEngine, RtcOptions

from .logger import setup_logger
from .realtime.struct import ErrorMessage, FunctionCallOutputItemParam, InputAudioBufferCommitted, InputAudioBufferSpeechStarted, InputAudioBufferSpeechStopped, InputAudioTranscription, ItemCreate, ItemCreated, ItemInputAudioTranscriptionCompleted, RateLimitsUpdated, ResponseAudioDelta, ResponseAudioDone, ResponseAudioTranscriptDelta, ResponseAudioTranscriptDone, ResponseContentPartAdded, ResponseContentPartDone, ResponseCreate, ResponseCreated, ResponseDone, ResponseFunctionCallArgumentsDelta, ResponseFunctionCallArgumentsDone, ResponseOutputItemAdded, ResponseOutputItemDone, ServerVADUpdateParams, SessionUpdate, SessionUpdateParams, SessionUpdated, Voices, to_json
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
    system_message: str | None = None
    turn_detection: ServerVADUpdateParams | None = None  # MARK: CHECK!
    voice: Voices | None = None


class RealtimeKitAgent:
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

        # Create audio interface with input callback
        self.audio_interface = AgoraAudioInterface(
            channel=self.channel, 
            loop=asyncio.get_running_loop()
        )
        
    async def run(self) -> None:
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
            
            # Start the audio interface with our input callback
            self.audio_interface.start(
                input_callback=lambda audio: asyncio.create_task(
                    self.connection.send_audio_data(audio)
                )
            )
            
            # Subscribe to audio
            await self.channel.subscribe_audio(self.subscribe_user)

            disconnected_future = asyncio.Future[None]()

            def callback(agora_rtc_conn: RTCConnection, conn_info: RTCConnInfo, reason):
                logger.info(f"Connection state changed: {conn_info.state}")
                if conn_info.state == 1:
                    if not disconnected_future.done():
                        disconnected_future.set_result(None)

            self.channel.on("connection_state_changed", callback)

            # Only need model_to_rtc now since audio_interface handles input
            asyncio.create_task(self.model_to_rtc()).add_done_callback(log_exception)
            asyncio.create_task(self._process_model_messages()).add_done_callback(
                log_exception
            )

            await disconnected_future
            logger.info("Agent finished running")
            
        except asyncio.CancelledError:
            logger.info("Agent cancelled")
        except Exception as e:
            logger.error(f"Error running agent: {e}")
            raise
        finally:
            # Make sure to stop the audio interface
            self.audio_interface.stop()

    async def model_to_rtc(self) -> None:
        """Handle audio output from the model to RTC"""
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
    """Agent that integrates ElevenLabs with Agora RTC"""
    
    def __init__(
        self,
        channel: Channel,
        config: ElevenLabsConfig,
        tools: Optional[ToolContext] = None,
        max_workers: int = 4  # Add thread pool size configuration
    ):
        self.channel = channel
        self.config = config
        self.tools = tools
        self.subscribe_user = None
        self.conversation: Optional[Conversation] = None
        self._disconnected_future: Optional[asyncio.Future] = None
        # Add thread pool for handling blocking operations
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ElevenLabsAgent"
        )
        self._start_time = time.time()

    async def _run_blocking(self, func, *args, **kwargs):
        """Run blocking function in thread pool with timing"""
        start_time = time.time()
        try:
            return await asyncio.get_running_loop().run_in_executor(
                self._thread_pool,
                partial(func, *args, **kwargs)
            )
        finally:
            duration = time.time() - start_time
            logger.debug(f"Operation '{func.__name__}' took {duration:.3f}s")

    async def setup(self):
        """Setup the agent with proper error handling and timing"""
        try:
            setup_start = time.time()
            logger.info("Starting agent setup")
            
            # Validate config first
            self.config.validate()
            
            # Setup logging based on config
            if self.config.debug_logging:
                logger.setLevel(logging.DEBUG)
            
            await self._setup_agent()
            
            setup_duration = time.time() - setup_start
            logger.info(f"Agent setup completed in {setup_duration:.3f}s")
            
        except Exception as e:
            error = handle_error(e)
            logger.error(f"Setup failed: {error.message}")
            if error.details:
                logger.debug(f"Error details: {error.details}")
            raise error

    async def run(self):
        """Run the agent with proper cleanup"""
        run_start = time.time()
        try:
            await self.setup()
            
            # Start conversation in background task
            conversation_task = asyncio.create_task(
                self._run_conversation(),
                name="ElevenLabs-Conversation"
            )
            
            # Wait for disconnect or conversation end
            await self._disconnected_future
            
        except Exception as e:
            logger.error(f"Error running agent: {e}")
            raise
        finally:
            await self.stop()
            run_duration = time.time() - run_start
            logger.info(f"Agent ran for {run_duration:.3f}s")

    async def stop(self):
        """Stop the agent and cleanup resources"""
        try:
            if self.conversation:
                await self.conversation.end_session()
                self.conversation = None
                
            if self.channel:
                await self.channel.disconnect()
                
            if self._disconnected_future and not self._disconnected_future.done():
                self._disconnected_future.set_result(None)
                
            # Shutdown thread pool
            self._thread_pool.shutdown(wait=True)
            
            total_duration = time.time() - self._start_time
            logger.info(f"Agent stopped after running for {total_duration:.3f}s")
            
        except Exception as e:
            logger.error(f"Error stopping agent: {e}")

    async def _run_conversation(self):
        """Run the conversation with proper error handling"""
        try:
            # Start conversation session
            await self.conversation.start_session()
            
            # Wait for session end
            conversation_id = await self.conversation.wait_for_session_end()
            if conversation_id:
                logger.info(f"Conversation ended with ID: {conversation_id}")
            
        except Exception as e:
            logger.error(f"Error in conversation: {e}")
            if not self._disconnected_future.done():
                self._disconnected_future.set_result(None)

    async def _setup_agent(self):
        """Internal setup implementation"""
        logger.info("Setting up ElevenLabs agent")
        
        # Wait for remote user
        self.subscribe_user = await self.wait_for_remote_user()
        logger.info(f"Subscribing to user {self.subscribe_user}")
        await self.channel.subscribe_audio(self.subscribe_user)
        
        # Create ElevenLabs client
        client = ElevenLabs(api_key=self.config.api_key)
        
        # Create audio interface
        audio_interface = AgoraAudioInterface(
            channel=self.channel, 
            loop=asyncio.get_running_loop()
        )
        
        # Create conversation configuration
        config_override = ConversationConfigOverride(
            voice_id=self.config.voice_id,
            model_id=self.config.model,
            temperature=self.config.temperature,
            stream=self.config.stream,
            latency_optimization=self.config.latency_optimization,
        )
        
        conversation_config = ConversationInitiationData(
            conversation_config_override=config_override.to_dict(),
            dynamic_variables=self.config.dynamic_variables or {},
            extra_body=self.config.extra_config or {}
        )
        
        logger.info(f"Using conversation config: {conversation_config}")
        
        # Create conversation with proper config
        self.conversation = AsyncConversation(
            Conversation(
                client=client,
                agent_id=self.config.agent_id,
                requires_auth=True,
                audio_interface=audio_interface,
                config=conversation_config,
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
