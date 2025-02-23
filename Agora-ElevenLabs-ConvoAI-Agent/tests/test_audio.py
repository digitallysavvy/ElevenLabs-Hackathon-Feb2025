import pytest
import numpy as np
from unittest.mock import Mock, AsyncMock
from realtime_agent.audio import AgoraAudioInterface
from realtime_agent.audio_buffer import AudioBufferManager

@pytest.fixture
def mock_channel():
    channel = Mock()
    channel.push_audio_frame = AsyncMock()
    channel.clear_sender_audio_buffer = Mock()
    return channel

@pytest.fixture
def audio_interface(mock_channel):
    return AgoraAudioInterface(channel=mock_channel)

def test_resample_audio(audio_interface):
    # Create test audio data
    test_audio = np.sin(np.linspace(0, 1000, 24000)).astype(np.int16).tobytes()
    
    # Test downsampling
    resampled = audio_interface._resample_audio(test_audio, 24000, 16000)
    assert len(resampled) == len(test_audio) * 2/3
    
    # Test upsampling
    resampled = audio_interface._resample_audio(test_audio, 16000, 24000)
    assert len(resampled) == len(test_audio) * 3/2

@pytest.mark.asyncio
async def test_audio_buffer():
    buffer = AudioBufferManager(max_buffer_size=2)
    
    # Test adding chunks
    chunk1 = b'test1'
    chunk2 = b'test2'
    buffer.add_chunk(chunk1)
    buffer.add_chunk(chunk2)
    
    # Test getting chunks
    assert await buffer.get_next_chunk() == chunk1
    assert await buffer.get_next_chunk() == chunk2
    
    # Test buffer clear
    buffer.clear()
    assert await buffer.get_next_chunk() is None

@pytest.mark.asyncio
async def test_audio_interface_lifecycle(audio_interface, mock_channel):
    # Test start
    callback = Mock()
    audio_interface.start(callback)
    assert audio_interface.is_running
    
    # Test output
    test_audio = b'test_audio'
    audio_interface.output(test_audio)
    
    # Test interrupt
    audio_interface.interrupt()
    mock_channel.clear_sender_audio_buffer.assert_called_once()
    
    # Test stop
    audio_interface.stop()
    assert not audio_interface.is_running 