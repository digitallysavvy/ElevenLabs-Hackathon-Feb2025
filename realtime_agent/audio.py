import asyncio
import logging
from collections import deque
from typing import Callable, Optional
import numpy as np
from scipy import signal

from elevenlabs.conversational_ai.conversation import AudioInterface
from .logger import setup_logger

logger = setup_logger(name=__name__, log_level=logging.INFO)

class AudioBufferManager:
    """Manages audio buffering for smooth playback and interruption handling"""
    
    def __init__(self, max_buffer_size: int = 50):
        self.buffer: deque[bytes] = deque(maxlen=max_buffer_size)
        self.buffer_event = asyncio.Event()
        self.is_playing = False
        self._current_chunk: Optional[bytes] = None
        
    def add_chunk(self, audio_chunk: bytes) -> None:
        """Add an audio chunk to the buffer"""
        try:
            self.buffer.append(audio_chunk)
            self.buffer_event.set()
        except Exception as e:
            logger.error(f"Error adding audio chunk to buffer: {e}")
        
    def clear(self) -> None:
        """Clear all buffered audio"""
        try:
            self.buffer.clear()
            self._current_chunk = None
            self.is_playing = False
        except Exception as e:
            logger.error(f"Error clearing audio buffer: {e}")
        
    async def get_next_chunk(self) -> Optional[bytes]:
        """Get the next audio chunk from the buffer"""
        try:
            if not self.buffer and not self.buffer_event.is_set():
                try:
                    await asyncio.wait_for(self.buffer_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    return None
                
            self.buffer_event.clear()
            
            if self.buffer:
                self._current_chunk = self.buffer.popleft()
                return self._current_chunk
                
            return None
        except Exception as e:
            logger.error(f"Error getting next audio chunk: {e}")
            return None

class AgoraAudioInterface(AudioInterface):
    """Custom AudioInterface implementation that works with Agora's audio system"""
    
    ELEVENLABS_SAMPLE_RATE = 16000  # ElevenLabs expects 16kHz input
    AGORA_SAMPLE_RATE = 24000       # Agora uses 24kHz
    INPUT_BUFFER_SIZE = 32000       # Buffer ~2 seconds of audio before sending
    
    def __init__(self, channel, buffer_size: int = 50, loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        Args:
            channel: Agora channel instance for audio I/O
            buffer_size: Maximum number of audio chunks to buffer
            loop: The asyncio event loop to use for scheduling asynchronous tasks.
                  Defaults to the running loop at instantiation.
        """
        super().__init__()  # Add parent class initialization
        self.channel = channel
        self._input_callback: Optional[Callable[[bytes], None]] = None
        self.is_running = False
        self.buffer_manager = AudioBufferManager(max_buffer_size=buffer_size)
        self._playback_task: Optional[asyncio.Task] = None
        self._input_task: Optional[asyncio.Task] = None  # Add task for input processing
        self._audio_lock = asyncio.Lock() # Add lock for thread safety
        # Capture the event loop passed in or get the current running loop
        self.loop = loop if loop is not None else asyncio.get_running_loop()
        self._frame_counter = 0  # Add counter for tracking frames
        self.remote_uid = None  # Store remote user ID
        self._input_buffer = bytearray()  # Buffer for collecting input audio
        self._silence_counter = 0  # Track silence frames
        self._is_speaking = False  # Track if we're currently capturing speech
        
    def _resample_audio(self, audio: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample audio between different sample rates using scipy with quality optimization"""
        try:
            logger.debug(f"Resampling audio: from={from_rate}Hz to={to_rate}Hz, size={len(audio)} bytes")
            
            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio, dtype=np.int16)
            
            # Use polyphase filtering for better quality
            gcd = np.gcd(to_rate, from_rate)
            up_factor = to_rate // gcd
            down_factor = from_rate // gcd
            
            resampled = signal.resample_poly(
                audio_array, 
                up=up_factor,
                down=down_factor,
                window=('kaiser', 5.0)
            )
            
            # Ensure the output is int16 and properly scaled
            resampled = np.clip(resampled, np.iinfo(np.int16).min, np.iinfo(np.int16).max)
            logger.debug(f"Resampling complete: output_size={len(resampled)} samples")
            
            return resampled.astype(np.int16).tobytes()
            
        except Exception as e:
            logger.error(f"Error resampling audio: {e}")
            return audio

    def start(self, input_callback: Callable[[bytes], None]):
        """Start processing audio"""
        try:
            self.is_running = True
            self._input_callback = input_callback
            self._frame_counter = 0
            
            # Get remote user ID from channel
            remote_users = list(self.channel.remote_users.keys())
            if remote_users:
                self.remote_uid = remote_users[0]
                logger.info(f"Found remote user: {self.remote_uid}")
            
            # Set up the audio frame callback
            def on_audio_frame(audio_frame):
                if not self.is_running:
                    return
                    
                try:
                    frame_size = len(audio_frame.data)
                    logger.info(f"Received raw audio frame from Agora: size={frame_size} bytes, frame_type={type(audio_frame)}")
                    
                    # Process the audio frame in the event loop
                    asyncio.run_coroutine_threadsafe(
                        self._handle_audio_frame(audio_frame), 
                        self.loop
                    )
                        
                except Exception as e:
                    logger.error(f"Error in audio frame callback: {e}", exc_info=True)
            
            # Set the callback directly
            self.channel.on_audio_frame = on_audio_frame
            logger.info("Audio frame callback set up successfully")
            
            # Start playback task
            self._playback_task = self.loop.create_task(self._playback_loop())
            
            logger.info("AgoraAudioInterface started successfully")
            
        except Exception as e:
            logger.error(f"Error starting AgoraAudioInterface: {e}")
            self.stop()
            raise

    async def _handle_audio_frame(self, audio_frame):
        """Handle incoming audio frame"""
        try:
            # Convert bytes to numpy array for resampling
            audio_array = np.frombuffer(audio_frame.data, dtype=np.int16)
            logger.info(f"Converting audio frame to numpy array: shape={audio_array.shape}, dtype={audio_array.dtype}")
            
            # Calculate resampling parameters
            gcd = np.gcd(self.ELEVENLABS_SAMPLE_RATE, self.AGORA_SAMPLE_RATE)
            up_factor = self.ELEVENLABS_SAMPLE_RATE // gcd
            down_factor = self.AGORA_SAMPLE_RATE // gcd
            
            # Resample using polyphase filtering
            resampled = signal.resample_poly(
                audio_array,
                up=up_factor,
                down=down_factor,
                window=('kaiser', 5.0)
            )
            
            # Ensure output is properly scaled and converted to int16
            resampled = np.clip(resampled, np.iinfo(np.int16).min, np.iinfo(np.int16).max)
            resampled_audio = resampled.astype(np.int16).tobytes()
            
            logger.info(f"Resampled audio frame: input_rate={self.AGORA_SAMPLE_RATE}Hz, "
                       f"output_rate={self.ELEVENLABS_SAMPLE_RATE}Hz, "
                       f"input_size={len(audio_frame.data)}, output_size={len(resampled_audio)}")
            
            if self._input_callback:
                # Run the callback in the event loop's executor to avoid blocking
                await self.loop.run_in_executor(
                    None,
                    self._input_callback,
                    resampled_audio
                )
                logger.info("Successfully sent resampled audio to ElevenLabs")
            
        except Exception as e:
            logger.error(f"Error handling audio frame: {e}", exc_info=True)

    def stop(self):
        """Stop processing audio"""
        try:
            self.is_running = False
            self._input_callback = None
            
            # Remove the callback
            self.channel.on_audio_frame = None
            
            self.remote_uid = None
            
            # Cancel playback task
            if self._playback_task:
                self._playback_task.cancel()
                self._playback_task = None
                
            # Clear buffer
            self.buffer_manager.clear()
            
            logger.info("AgoraAudioInterface stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping AgoraAudioInterface: {e}")

    def output(self, audio: bytes):
        """Output audio through Agora"""
        if not self.is_running:
            return
            
        try:
            logger.info(f"Received audio from ElevenLabs: size={len(audio)} bytes")
            self.buffer_manager.add_chunk(audio)
            
        except Exception as e:
            logger.error(f"Error in audio output: {e}")

    async def _playback_loop(self):
        """Continuous loop for playing buffered audio with quality monitoring"""
        while self.is_running:
            try:
                chunk = await self.buffer_manager.get_next_chunk()
                if chunk:
                    self._frame_counter += 1
                    logger.debug(  # Changed to DEBUG level
                        f"Playing audio chunk #{self._frame_counter} through Agora: "
                        f"size={len(chunk)} bytes"
                    )
                    # Ensure we're using the correct event loop
                    await self.channel.push_audio_frame(chunk)
                    await asyncio.sleep(0.01)  # Small delay to prevent flooding
            except Exception as e:
                logger.error(f"Error in playback loop: {e}")
                await asyncio.sleep(0.1)
                
    def interrupt(self):
        """Handle interruption of audio output"""
        try:
            logger.info("Interrupting audio playback")
            # Clear any pending audio in buffer
            self.buffer_manager.clear()
            
            # Clear Agora's buffer
            self.channel.clear_sender_audio_buffer()
            
            logger.info("Audio playback interrupted successfully")
        except Exception as e:
            logger.error(f"Error interrupting audio: {e}") 