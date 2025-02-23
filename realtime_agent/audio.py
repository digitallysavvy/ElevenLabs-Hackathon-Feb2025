import asyncio
import logging
from collections import deque
from typing import Callable, Optional
import numpy as np
from scipy import signal
import base64
from concurrent.futures import ThreadPoolExecutor
import websockets

from elevenlabs.conversational_ai.conversation import AudioInterface
from .logger import setup_logger

logger = setup_logger(name=__name__, log_level=logging.INFO)

"""
Audio interface implementation for handling real-time audio streaming between Agora and ElevenLabs.
This module manages bidirectional audio communication, including buffering, resampling, and 
interruption handling for smooth audio playback and recording.
"""

class AudioBufferManager:
    """Manages audio buffering for smooth playback and interruption handling.
    Implements a thread-safe queue system for audio chunks with event-based synchronization.
    """
    
    def __init__(self, max_buffer_size: int = 50):
        # Maximum number of audio chunks that can be stored in the buffer
        self.buffer: deque[bytes] = deque(maxlen=max_buffer_size)
        # Event to signal when new audio is available in the buffer
        self.buffer_event = asyncio.Event()
        # Flag to track if audio is currently being played
        self.is_playing = False
        # Currently processing audio chunk
        self._current_chunk: Optional[bytes] = None
        
    def add_chunk(self, audio_chunk: bytes) -> None:
        """Add an audio chunk to the buffer"""
        try:
            self.buffer.append(audio_chunk)
            self.buffer_event.set()
        except Exception as e:
            logger.error(f"Error adding audio chunk to buffer: {e}")
        
    def clear(self) -> None:
        """Clear all buffered audio and reset playback state.
        
        Used when needing to immediately stop playback and discard pending audio.
        """
        try:
            self.buffer.clear()
            self._current_chunk = None
            self.is_playing = False
        except Exception as e:
            logger.error(f"Error clearing audio buffer: {e}")
        
    async def get_next_chunk(self) -> Optional[bytes]:
        """Get the next audio chunk from the buffer with timeout handling.
        
        Returns None if no audio is available after timeout (0.5s).
        Uses event-based synchronization to efficiently wait for new audio.
        """
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
    """Custom AudioInterface implementation for Agora's real-time communication system.
    Handles audio I/O between Agora SDK and ElevenLabs, including sample rate conversion
    and buffering for smooth playback.
    """
    
    # Class constants with explanatory comments
    ELEVENLABS_SAMPLE_RATE = 16000  # ElevenLabs expects 16kHz input
    AGORA_SAMPLE_RATE = 24000       # Agora uses 24kHz
    INPUT_BUFFER_SIZE = 4000        # Match ElevenLabs expected buffer size (250ms @ 16kHz)
    
    def __init__(self, channel, remote_uid: int, buffer_size: int = 50, loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        Args:
            channel: Agora channel instance for audio I/O
            remote_uid: The remote user ID to get audio from
            buffer_size: Maximum number of audio chunks to buffer
            loop: The asyncio event loop to use for scheduling asynchronous tasks.
                  Defaults to the running loop at instantiation.
        """
        super().__init__() # Parent class initialization
        self.channel = channel
        self.remote_uid = remote_uid
        self._input_callback: Optional[Callable[[bytes], None]] = None
        self.is_running = False
        self.buffer_manager = AudioBufferManager(max_buffer_size=buffer_size)
        self._playback_task: Optional[asyncio.Task] = None
        self._input_task: Optional[asyncio.Task] = None
        self.loop = loop if loop is not None else asyncio.get_running_loop()
        self._input_buffer = bytearray()
        self._chunks_sent = 0
        # Add a queue for thread-safe audio chunk handling
        self._audio_queue = asyncio.Queue()
        self._input_processor_task = None

    def _resample_audio(self, audio_data: bytes, src_rate: int, dst_rate: int) -> bytes:
        # Convert raw bytes (16-bit PCM) into a NumPy array
        samples = np.frombuffer(audio_data, dtype=np.int16)
        
        # Calculate the expected new number of samples
        new_length = int(len(samples) * dst_rate / src_rate)
        
        # Convert samples to float32 for resampling
        float_samples = samples.astype(np.float32)
        
        # Do the resampling
        resampled = signal.resample(float_samples, new_length)
        
        # Clip and convert back to int16
        resampled_int16 = np.clip(resampled, -32768, 32767).astype(np.int16)
        return resampled_int16.tobytes()

    def start(self, input_callback: Callable[[bytes], None]):
        """Start processing audio with the given input callback"""
        try:
            logger.info("Starting AgoraAudioInterface")
            self.is_running = True
            self._input_callback = input_callback
            logger.info(f"Input callback set: {input_callback is not None}")
            
            # Start the input processing tasks
            if self._input_task is None:
                self._input_task = self.loop.create_task(self._process_input())
                self._input_processor_task = self.loop.create_task(self._process_audio_queue())
                logger.info("Created input processing tasks")
            
            # Start the playback task if not already running
            if self._playback_task is None:
                self._playback_task = self.loop.create_task(self._playback_loop())
                logger.info("Created playback task")
            
        except Exception as e:
            logger.error(f"Error starting audio interface: {e}")
            raise

    async def _process_audio_queue(self):
        """Process audio chunks from the queue and send them to ElevenLabs"""
        while self.is_running:
            try:
                chunk = await self._audio_queue.get()
                if self._input_callback:
                    try:
                        # Log the chunk being sent
                        logger.info(f"Sending audio chunk to ElevenLabs, size: {len(chunk)} bytes")
                        
                        # Check if chunk contains actual audio data
                        if any(b != 0 for b in chunk):
                            logger.info("Chunk contains non-zero audio data")
                        else:
                            logger.warning("Chunk contains only zeros!")
                            
                        self._input_callback(chunk)
                        self._chunks_sent += 1
                        logger.info(f"Successfully sent chunk {self._chunks_sent} to ElevenLabs")
                        
                        # Use a consistent delay between chunks
                        await asyncio.sleep(0.25)  # 250ms matches the chunk duration
                    except Exception as e:
                        logger.error(f"Error in input callback: {e}", exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing audio queue: {e}", exc_info=True)
                await asyncio.sleep(0.1)

    async def _process_input(self):
        """Continuously process incoming audio frames from Agora"""
        logger.info(f"Starting input processing for remote user {self.remote_uid}")
        
        while self.is_running:
            try:
                # Get audio frames from the channel
                audio_frames = self.channel.get_audio_frames(self.remote_uid)
                if not audio_frames:
                    logger.debug("Waiting for audio frames to become available...")
                    await asyncio.sleep(0.1)
                    continue
                
                # Check if audio_frames is an async iterator
                if not hasattr(audio_frames, '__aiter__'):
                    logger.error(f"Audio frames object is not an async iterator: {type(audio_frames)}")
                    await asyncio.sleep(0.1)
                    continue
                
                logger.info(f"Audio frames available for user {self.remote_uid}")
                logger.info(f"Received audio frames from Agora: {type(audio_frames)}")
                
                async for audio_frame in audio_frames:
                    if not self.is_running:
                        break
                    
                    try:
                        # Log the raw audio frame data
                        logger.info(f"Raw audio frame size: {len(audio_frame.data)} bytes")
                        
                        # Process the audio frame
                        resampled_audio = self._resample_audio(
                            audio_frame.data, 
                            self.AGORA_SAMPLE_RATE, 
                            self.ELEVENLABS_SAMPLE_RATE
                        )
                        
                        # Log the resampled audio size
                        logger.info(f"Resampled audio size: {len(resampled_audio)} bytes")
                        
                        # Add to input buffer
                        self._input_buffer.extend(resampled_audio)
                        
                        # Log buffer size
                        logger.info(f"Current input buffer size: {len(self._input_buffer)} bytes")
                        
                        # If we have enough samples, queue for processing
                        while len(self._input_buffer) >= self.INPUT_BUFFER_SIZE * 2:
                            chunk = bytes(self._input_buffer[:self.INPUT_BUFFER_SIZE * 2])
                            logger.info(f"Queuing chunk of size: {len(chunk)} bytes")
                            await self._audio_queue.put(chunk)
                            self._input_buffer = self._input_buffer[self.INPUT_BUFFER_SIZE * 2:]
                    
                    except Exception as frame_error:
                        logger.error(f"Error processing audio frame: {frame_error}", exc_info=True)
                        continue
                    
            except Exception as e:
                logger.error(f"Error in input processing loop: {e}", exc_info=True)
                if self.is_running:
                    # Restart the processing if it wasn't intentionally stopped
                    logger.info("Restarting input processing...")
                    await asyncio.sleep(1)  # Add delay before restart

    def stop(self):
        """Stop processing audio"""
        try:
            self.is_running = False
            self._input_callback = None
            
            # Cancel tasks
            for task in [self._input_task, self._playback_task, self._input_processor_task]:
                if task:
                    task.cancel()
            
            self._input_task = None
            self._playback_task = None
            self._input_processor_task = None
            
            # Clear buffer
            self.buffer_manager.clear()
            self._input_buffer.clear()
            
            logger.info("AgoraAudioInterface stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping AgoraAudioInterface: {e}")

    def output(self, audio: bytes):
        """Queue audio received from ElevenLabs for playback through Agora.
        
        The audio is added to a buffer for smooth playback and to handle
        potential network jitter.
        """
        if not self.is_running:
            return
            
        try:
            logger.info(f"Received audio from ElevenLabs: size={len(audio)} bytes")
            self.buffer_manager.add_chunk(audio)
            
        except Exception as e:
            logger.error(f"Error in audio output: {e}")

    async def _playback_loop(self):
        """Continuous loop that handles playing buffered audio through Agora.
        
        - Retrieves audio chunks from buffer
        - Pushes audio to Agora channel
        - Implements rate limiting to prevent buffer overflow
        - Handles playback errors gracefully
        """
        while self.is_running:
            try:
                chunk = await self.buffer_manager.get_next_chunk()
                if chunk:
                    logger.debug(  # Changed to DEBUG level
                        f"Playing audio chunk through Agora: size={len(chunk)} bytes"
                    )
                    # Ensure we're using the correct event loop
                    await self.channel.push_audio_frame(chunk)
                    await asyncio.sleep(0.01)  # Small delay to prevent flooding
            except Exception as e:
                logger.error(f"Error in playback loop: {e}")
                await asyncio.sleep(0.1)
                
    def interrupt(self):
        """Immediately stop audio playback and clear all buffers.
        
        Used when needing to stop current audio playback, such as when
        the AI is interrupted by the user.
        """
        try:
            logger.info("Interrupting audio playback")
            # Clear any pending audio in buffer
            self.buffer_manager.clear()
            
            # Clear Agora's buffer
            self.channel.clear_sender_audio_buffer()
            
            logger.info("Audio playback interrupted successfully")
        except Exception as e:
            logger.error(f"Error interrupting audio: {e}") 