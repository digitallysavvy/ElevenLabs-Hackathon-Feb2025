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
        self._input_task: Optional[asyncio.Task] = None
        self.loop = loop if loop is not None else asyncio.get_running_loop()
        self.remote_uid = None
        self._frame_queue: asyncio.Queue[bytes] = asyncio.Queue()
        
    def _resample_audio(self, audio: bytes, from_rate: int, to_rate: int) -> bytes:
        """Resample audio between different sample rates using scipy with quality optimization"""
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
        """Start processing audio"""
        try:
            self.is_running = True
            self._input_callback = input_callback
            
            # Get remote user ID from channel
            remote_users = list(self.channel.remote_users.keys())
            if remote_users:
                self.remote_uid = remote_users[0]
                logger.info(f"Found remote user: {self.remote_uid}")
            
            # Start the input processing task
            logger.info("Starting input processing task")
            self._input_task = self.loop.create_task(self._process_input())
            
            # Start playback task
            logger.info("Starting playback task")
            self._playback_task = self.loop.create_task(self._playback_loop())
            
            logger.info("AgoraAudioInterface started successfully")
            
        except Exception as e:
            logger.error(f"Error starting AgoraAudioInterface: {e}")
            self.stop()
            raise

    async def _process_input(self):
        """Process input audio frames using get_audio_frames"""
        logger.info("Starting input processing")
        
        # Wait for both remote_uid and audio subscription to be ready
        while True:
            if not self.is_running:
                return
            
            if self.remote_uid is None:
                logger.debug("Waiting for remote_uid...")
                await asyncio.sleep(0.1)
                continue
            
            # Check if we can get audio frames
            audio_frames = self.channel.get_audio_frames(self.remote_uid)
            if audio_frames is None:
                logger.debug("Waiting for audio frames to become available...")
                await asyncio.sleep(0.1)
                continue
            
            # Add this check
            if not hasattr(audio_frames, '__aiter__'):
                logger.error(f"Audio frames object is not an async iterator: {type(audio_frames)}")
                await asyncio.sleep(0.1)
                continue
            
            logger.info(f"Audio frames available for user {self.remote_uid}")
            break
        
        try:
            frame_count = 0
            logger.info("Starting audio frame processing loop")  # Add this log
            async for audio_frame in audio_frames:
                if not self.is_running:
                    break
                
                frame_count += 1
                try:
                    # Log raw frame details
                    input_samples = len(audio_frame.data) // 2
                    logger.info(f"Frame {frame_count}: Received raw audio frame: "
                              f"size={len(audio_frame.data)} bytes ({input_samples} samples)")
                    
                    # Resample the audio
                    resampled_audio = self._resample_audio(
                        audio_frame.data, 
                        self.AGORA_SAMPLE_RATE, 
                        self.ELEVENLABS_SAMPLE_RATE
                    )
                    
                    output_samples = len(resampled_audio) // 2
                    logger.info(f"Frame {frame_count}: Resampled audio: "
                              f"size={len(resampled_audio)} bytes ({output_samples} samples)")
                    
                    if self._input_callback:
                        # Instead of awaiting, run the callback directly since it's not async
                        self._input_callback(resampled_audio)
                        logger.info(f"Frame {frame_count}: Successfully sent audio frame to model")
                    
                except Exception as frame_error:
                    logger.error(f"Frame {frame_count}: Error processing audio frame: {frame_error}")
                    continue
                
        except Exception as e:
            logger.error(f"Error in input processing loop: {e}", exc_info=True)
            if self.is_running:
                # Restart the processing if it wasn't intentionally stopped
                logger.info("Restarting input processing...")
                self._input_task = self.loop.create_task(self._process_input())

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