# Quickstart

In this example we will create a simple script that runs a conversation with the ElevenLabs Conversational AI agent. You can find the full code in the ElevenLabs examples repository.

First import the necessary dependencies:

```bash
import os
import signal

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
```

Next load the agent ID and API key from environment variables:

```bash
agent_id = os.getenv("AGENT_ID")
api_key = os.getenv("ELEVENLABS_API_KEY")
```

The API key is only required for non-public agents that have authentication enabled. You don’t have to set it for public agents and the code will work fine without it.

Then create the ElevenLabs client instance:

```bash
client = ElevenLabs(api_key=api_key)
```

Now we initialize the Conversation instance:

```bash
conversation = Conversation(
    # API client and agent ID.
    client,
    agent_id,

    # Assume auth is required when API_KEY is set.
    requires_auth=bool(api_key),

    # Use the default audio interface.
    audio_interface=DefaultAudioInterface(),

    # Simple callbacks that print the conversation to the console.
    callback_agent_response=lambda response: print(f"Agent: {response}"),
    callback_agent_response_correction=lambda original, corrected: print(f"Agent: {original} -> {corrected}"),
    callback_user_transcript=lambda transcript: print(f"User: {transcript}"),

    # Uncomment if you want to see latency measurements.
    # callback_latency_measurement=lambda latency: print(f"Latency: {latency}ms"),
)
```

We are using the DefaultAudioInterface which uses the default system audio input/output devices for the conversation. You can also implement your own audio interface by subclassing elevenlabs.conversational_ai.conversation.AudioInterface.

Now we can start the conversation:

```bash
conversation.start_session()
```

To get a clean shutdown when the user presses Ctrl+C we can add a signal handler which will call end_session():

```bash
signal.signal(signal.SIGINT, lambda sig, frame: conversation.end_session())
```

And lastly we wait for the conversation to end and print out the conversation ID (which can be used for reviewing the conversation history and debugging):

```bash
conversation_id = conversation.wait_for_session_end()
print(f"Conversation ID: {conversation_id}")
```

All that is left is to run the script and start talking to the agent:

```bash
# For public agents:
AGENT_ID=youragentid python demo.py

# For private agents:
AGENT_ID=youragentid ELEVENLABS_API_KEY=yourapikey python demo.py
```
