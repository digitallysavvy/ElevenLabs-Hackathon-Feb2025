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
        self.thread_pool = ThreadPoolExecutor(max_workers=1)

    def _resample_audio(self, audio: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample audio between different sample rates using high-quality polyphase filtering.
        
        Args:
            audio: Raw audio bytes in int16 format
            from_rate: Original sample rate in Hz
            to_rate: Target sample rate in Hz
            
        Returns:
            Resampled audio bytes in int16 format
        """
        try:
            input_samples = len(audio) // 2  # Since we're using int16, each sample is 2 bytes
            expected_output_samples = int(input_samples * (to_rate / from_rate))
            
            logger.info(f"Resampling audio: from={from_rate}Hz ({input_samples} samples) "
                       f"to={to_rate}Hz (expect {expected_output_samples} samples)")
            
            # Convert bytes to numpy array
            audio_array = np.frombuffer(audio, dtype=np.int16)
            
            # Use polyphase filtering for better quality
            gcd = np.gcd(to_rate, from_rate)
            up_factor = to_rate // gcd
            down_factor = from_rate // gcd
            
            logger.info(f"Resample factors: up={up_factor}, down={down_factor} (GCD={gcd})")
            
            resampled = signal.resample_poly(
                audio_array, 
                up=up_factor,
                down=down_factor,
                window=('kaiser', 5.0)
            )
            
            # Ensure the output is int16 and properly scaled
            resampled = np.clip(resampled, np.iinfo(np.int16).min, np.iinfo(np.int16).max)
            actual_output_samples = len(resampled)
            
            logger.info(f"Resampling complete: got {actual_output_samples} samples "
                       f"(expected {expected_output_samples})")
            
            # Validate the resampling ratio
            actual_ratio = actual_output_samples / input_samples
            expected_ratio = to_rate / from_rate
            if not np.isclose(actual_ratio, expected_ratio, rtol=0.1):
                logger.error(f"Resampling ratio mismatch: got {actual_ratio:.3f}, "
                            f"expected {expected_ratio:.3f}")
            
            return resampled.astype(np.int16).tobytes()
            
        except Exception as e:
            logger.error(f"Error resampling audio: {e}", exc_info=True)
            return audio

    def start(self, input_callback: Callable[[bytes], None]):
        """Start processing audio with the given input callback"""
        try:
            logger.info("Starting AgoraAudioInterface")
            self.is_running = True
            self._input_callback = input_callback
            logger.info(f"Input callback set: {input_callback is not None}")
            
            # Start the input processing task
            if self._input_task is None:
                self._input_task = self.loop.create_task(self._process_input())
                logger.info("Created input processing task")
            
            # Start the playback task if not already running
            if self._playback_task is None:
                self._playback_task = self.loop.create_task(self._playback_loop())
                logger.info("Created playback task")
            
        except Exception as e:
            logger.error(f"Error starting audio interface: {e}")
            raise

    async def _process_input(self):
        """Continuously process incoming audio frames from Agora.
        
        - Retrieves audio frames from the Agora channel
        - Resamples audio from Agora's 24kHz to ElevenLabs' 16kHz
        - Buffers audio until enough samples are collected
        - Sends audio chunks to ElevenLabs via callback
        - Handles connection errors and automatic recovery
        """
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
                
                async for audio_frame in audio_frames:
                    if not self.is_running:
                        break
                    
                    try:
                        # Process the audio frame
                        resampled_audio = self._resample_audio(
                            audio_frame.data, 
                            self.AGORA_SAMPLE_RATE, 
                            self.ELEVENLABS_SAMPLE_RATE
                        )
                        
                        # Add to input buffer
                        self._input_buffer.extend(resampled_audio)
                        
                        # If we have enough samples, send to ElevenLabs
                        while len(self._input_buffer) >= self.INPUT_BUFFER_SIZE * 2:  # *2 because 16-bit samples
                            if self._input_callback:
                                # Send raw audio chunk directly
                                chunk = bytes(self._input_buffer[:self.INPUT_BUFFER_SIZE * 2])
                                try:
                                    # Log before sending
                                    logger.info(f"About to send chunk to callback, buffer size: {len(chunk)} bytes")
                                    
                                    # Call the callback in a thread-safe way
                                    await self.loop.run_in_executor(
                                        self.thread_pool,
                                        self._input_callback,
                                        chunk
                                    )
                                    self._chunks_sent += 1
                                    logger.info(f"Successfully sent chunk {self._chunks_sent} to ElevenLabs")
                                except (ConnectionError, websockets.exceptions.ConnectionClosed) as e:
                                    logger.error(f"WebSocket connection error: {e}")
                                    # Signal that we need to reconnect
                                    if hasattr(self, '_on_connection_error'):
                                        await self._on_connection_error()
                                    break
                                except Exception as e:
                                    logger.error(f"Error in input_callback: {e}", exc_info=True)
                                finally:
                                    # Remove the processed samples from buffer
                                    self._input_buffer = self._input_buffer[self.INPUT_BUFFER_SIZE * 2:]
                            else:
                                logger.warning("No input callback set, dropping audio chunk")
                    
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
            if self._input_task:
                self._input_task.cancel()
                self._input_task = None
                
            if self._playback_task:
                self._playback_task.cancel()
                self._playback_task = None
                
            # Clear buffer
            self.buffer_manager.clear()
            
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