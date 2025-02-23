"use client";

import { useState, useMemo } from "react";
import AgoraRTC, { AgoraRTCProvider } from "agora-rtc-react";
// import ParticleBackground from './ParticleBackground';
import Background from "./Background";
import ConversationComponent from "./ConversationComponent";

interface AgoraTokenData {
  token: string;
  uid: string;
  channel: string;
  clientID?: string;
}

export default function LandingPage() {
  const [showConversation, setShowConversation] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agoraData, setAgoraData] = useState<AgoraTokenData | null>(null);
  const [agentJoinError, setAgentJoinError] = useState(false);

  // Create client once
  const agoraClient = useMemo(
    () => AgoraRTC.createClient({ mode: "rtc", codec: "vp8" }),
    []
  );

  const handleStartConversation = async () => {
    setIsLoading(true);
    setError(null);
    setAgentJoinError(false);

    try {
      // First, get the Agora token
      console.log("Fetching Agora token...");
      const agoraResponse = await fetch("/api/generate-agora-token");
      const responseData = await agoraResponse.json();
      console.log("Agora API response:", responseData);

      if (!agoraResponse.ok) {
        throw new Error(
          `Failed to generate Agora token: ${JSON.stringify(responseData)}`
        );
      }

      setAgoraData(responseData);

      // Send the channel name when starting the conversation
      try {
        const response = await fetch("/api/start-conversation", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            channel_name: responseData.channel,
          }),
        });

        if (!response.ok) {
          setAgentJoinError(true);
        } else {
          const agentData = await response.json();
          setAgoraData({ ...responseData, clientID: agentData.clientID });
        }
      } catch (err) {
        console.error("Failed to start conversation with agent:", err);
        setAgentJoinError(true);
      }

      setShowConversation(true);
    } catch (err) {
      setError("Failed to start conversation. Please try again.");
      console.error("Error starting conversation:", err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleTokenWillExpire = async (uid: string) => {
    try {
      const response = await fetch(
        `/api/generate-agora-token?channel=${agoraData?.channel}&uid=${uid}`
      );
      const data = await response.json();

      if (!response.ok) {
        throw new Error("Failed to generate new token");
      }

      return data.token;
    } catch (error) {
      console.error("Error renewing token:", error);
      throw error;
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-900 text-white relative overflow-hidden">
      {/* <ParticleBackground /> */}
      <Background />
      <div className="z-10 text-center">
        <h1 className="text-4xl font-bold mb-6">Converse</h1>
        <p className="text-lg mb-6">
          When was the last time you hand an intelligent conversation?
        </p>
        {!showConversation ? (
          <>
            <button
              onClick={handleStartConversation}
              disabled={isLoading}
              className="px-8 py-3 bg-blue-600/80 text-white rounded-full border border-blue-400/30 backdrop-blur-sm 
              hover:bg-blue-700/90 transition-all shadow-lg hover:shadow-blue-500/20 
              disabled:opacity-50 disabled:cursor-not-allowed text-lg font-medium"
            >
              {isLoading ? "Starting..." : "Start Conversation"}
            </button>
            {error && <p className="mt-4 text-red-400">{error}</p>}
          </>
        ) : agoraData ? (
          <>
            {agentJoinError && (
              <div className="mb-4 p-3 bg-red-600/20 rounded-lg text-red-400">
                Failed to connect with AI agent. The conversation may not work
                as expected.
              </div>
            )}
            <AgoraRTCProvider client={agoraClient}>
              <ConversationComponent
                agoraData={agoraData}
                onTokenWillExpire={handleTokenWillExpire}
                onEndConversation={() => setShowConversation(false)}
              />
            </AgoraRTCProvider>
          </>
        ) : (
          <p>Failed to load conversation data.</p>
        )}
      </div>
    </div>
  );
}
