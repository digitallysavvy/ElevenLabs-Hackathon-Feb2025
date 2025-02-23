# Custom Conversational AI Agents

Conversational AI Agents are semi-autonomous microservices that ingest audio streams with direct connections to large language models to process user conversations and return audio-native responses. This repository contains the scripting to setup the infrastructure for the agent servers.

For demo purposes we've created a simple [client](../Agora-ElevenLabs-Client/) that you can use to test the agent.

Supported Hosting Services:

- [AWS](aws/README.md)
- Azure **([In Progress](https://github.com/AgoraIO-Community/Custom-Conversational-AI-Agent-Deployer/tree/azure/azure))**
- [DigitalOcean](digital-ocean/README.md)
- GCP **(Coming Soon)**

## Prerequisites

- [Agora Account](https://www.agora.io/en/signup/)
- [ElevenLabs Account](https://elevenlabs.io/app/sign-in)
- [Pulumi CLI](https://www.pulumi.com/docs/get-started/install/)
- [Node.js](https://nodejs.org/) (v14 or later)

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/digitallysavvy/ElevenLabs-Hackathon-Feb2025
   cd ElevenLabs-Hackathon-Feb2025/Agora-ElevenLabs-Deployer
   ```

2. Navigate to the folder for the platform you want to deploy to:

   AWS

   ```bash
   cd aws
   ```

   DigitalOcean

   ```bash
   cd digital-ocean
   ```

3. Open the platform's `README.md` file and follow the instructions to configure the secrets and deploy the infrastructure.

## Scaling

Currently the deployment is set up to run 3 agent instances on relatively modest hardware. This results in a maximum of 11-16 concurrent conversations per agent server and a maximum of 33-48 concurrent conversations.

We set the default maximum number of requests per backend to 11 to avoid overloading the agent instances, which results in a maximum of 33 concurrent conversations.

To scale up there are a two options that can be used separately or together:

1. Use more powerful instances for the agent instances
2. Increase the number of agent instances

## Contributing

We welcome contributions to this project. Please see the [CONTRIBUTING.md](CONTRIBUTING.md) file for more information.
