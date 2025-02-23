'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  useLocalMicrophoneTrack,
  useRTCClient,
  usePublish,
  IMicrophoneAudioTrack,
} from 'agora-rtc-react';
import { Mic, MicOff } from 'lucide-react';

interface AudioBar {
  height: number;
}

interface MicrophoneButtonProps {
  isEnabled: boolean;
  setIsEnabled: (enabled: boolean) => void;
  localMicrophoneTrack: IMicrophoneAudioTrack | null;
}

export function MicrophoneButton({
  isEnabled,
  setIsEnabled,
  localMicrophoneTrack,
}: MicrophoneButtonProps) {
  const [audioData, setAudioData] = useState<AudioBar[]>(
    Array(5).fill({ height: 0 })
  );
  const client = useRTCClient();
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number>();

  useEffect(() => {
    if (localMicrophoneTrack && isEnabled) {
      setupAudioAnalyser();
    } else {
      cleanupAudioAnalyser();
    }

    return () => cleanupAudioAnalyser();
  }, [localMicrophoneTrack, isEnabled]);

  const setupAudioAnalyser = async () => {
    if (!localMicrophoneTrack) return;

    try {
      audioContextRef.current = new AudioContext();
      analyserRef.current = audioContextRef.current.createAnalyser();
      analyserRef.current.fftSize = 64;
      analyserRef.current.smoothingTimeConstant = 0.5;

      const mediaStream = localMicrophoneTrack.getMediaStreamTrack();
      const source = audioContextRef.current.createMediaStreamSource(
        new MediaStream([mediaStream])
      );
      source.connect(analyserRef.current);

      updateAudioData();
    } catch (error) {
      console.error('Error setting up audio analyser:', error);
    }
  };

  const cleanupAudioAnalyser = () => {
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setAudioData(Array(5).fill({ height: 0 }));
  };

  const updateAudioData = () => {
    if (!analyserRef.current) return;

    const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
    analyserRef.current.getByteFrequencyData(dataArray);

    const segmentSize = Math.floor(dataArray.length / 5);
    const newAudioData = Array(5)
      .fill(0)
      .map((_, index) => {
        const start = index * segmentSize;
        const end = start + segmentSize;
        const segment = dataArray.slice(start, end);
        const average = segment.reduce((a, b) => a + b, 0) / segment.length;

        const scaledHeight = Math.min(60, (average / 255) * 100 * 1.2);
        const height = Math.pow(scaledHeight / 60, 0.7) * 60;

        return {
          height: height,
        };
      });

    setAudioData(newAudioData);
    animationFrameRef.current = requestAnimationFrame(updateAudioData);
  };

  const toggleMicrophone = async () => {
    if (localMicrophoneTrack) {
      const newState = !isEnabled;
      try {
        await localMicrophoneTrack.setEnabled(newState);
        if (!newState) {
          await client.unpublish(localMicrophoneTrack);
        } else {
          await client.publish(localMicrophoneTrack);
        }
        setIsEnabled(newState);
        console.log('Microphone state updated successfully');
      } catch (error) {
        console.error('Failed to toggle microphone:', error);
        localMicrophoneTrack.setEnabled(isEnabled);
      }
    }
  };

  return (
    <button
      onClick={toggleMicrophone}
      className={`relative w-16 h-16 rounded-full shadow-lg flex items-center justify-center transition-colors ${
        isEnabled ? 'bg-white hover:bg-gray-50' : 'bg-red-500 hover:bg-red-600'
      }`}
    >
      <div className="absolute inset-0 flex items-center justify-center gap-1">
        {audioData.map((bar, index) => (
          <div
            key={index}
            className="w-1 rounded-full transition-all duration-100"
            style={{
              height: `${bar.height}%`,
              backgroundColor: isEnabled ? '#22c55e' : '#94a3b8',
              transform: `scaleY(${Math.max(0.1, bar.height / 100)})`,
              transformOrigin: 'center',
            }}
          />
        ))}
      </div>

      <div className={`relative z-10`}>
        {isEnabled ? (
          <Mic size={24} className="text-gray-800" />
        ) : (
          <MicOff size={24} className="text-white" />
        )}
      </div>
    </button>
  );
}
