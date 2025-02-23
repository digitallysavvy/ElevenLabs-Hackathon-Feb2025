# Custom Conversational AI Agent

This folder contains the infrastructure as code for deploying a custom conversational agent. The infrastructure deploys a scalable system on DigitalOcean that includes agent instances, a proxy router, and a Redis database, managed using Pulumi.

## Prerequisites

- [Pulumi CLI](https://www.pulumi.com/docs/get-started/install/)
- [Node.js](https://nodejs.org/) (v14 or later)
- [DigitalOcean Account](https://www.digitalocean.com/)
- [Docker](https://www.docker.com/get-started) (for local development)
- [Agora](https://console.agora.io/en/)
- [ElevenLabs](https://elevenlabs.io/app/sign-in)

## Configuration

1. Set up your Pulumi stack:

```bash
pulumi stack init dev
```

2. Configure the required secrets:

```bash
pulumi config set --secret digitalocean:token <YOUR_DIGITAL_OCEAN_API_TOKEN>
pulumi config set --secret agoraAppId <YOUR_AGORA_APP_ID>
pulumi config set --secret agoraAppCert <YOUR_AGORA_APP_CERT>
pulumi config set --secret elevenLabsApiKey <YOUR_ELEVENLABS_API_KEY>
pulumi config set --secret elevenLabsAgentID <YOUR_ELEVENLABS_AGENT_ID>
pulumi config set --secret elevenLabsVoiceID <YOUR_ELEVENLABS_VOICE_ID>
pulumi config set elevenLabsModel <YOUR_ELEVENLABS_MODEL>
pulumi config set systemInstruction "Your custom system prompt here..."
```

Add your SSH key fingerprint to the `index.ts` file:

```bash
const sshKeys = [''] // Replace with your DO SSH key fingerprint
```

3. Deploy using Pulumi:

```bash
pulumi preview  # Review changes
pulumi up       # Deploy infrastructure
```

## Deployment

1. Preview the changes:

```bash
pulumi preview
```

2. Deploy the infrastructure using DigitalOcean:

```bash
pulumi up
```

## Cleanup

To destroy the infrastructure:

```bash
pulumi destroy
```

## Infrastructure Outputs

After deployment, you can access important information using:

```bash
pulumi stack output
```

This will show:

- Redis URI
- Agent IP addresses
- Proxy Router IP address

## Development

The infrastructure code is in `index.ts` and includes:

- Container registry setup
- Agent droplet creation
- Proxy router configuration
- Network and security settings

## Architecture

The infrastructure consists of:

- Container Registry: Hosts Docker images for the proxy router and realtime agents
- Multiple Agent Instances (3 droplets):
  - Handles ElevenLabs API communication
  - Runs on c-4 instances (4 vCPUs, 8GB RAM)
  - Containerized using Docker
- Proxy Router:
  - Load balances requests across agent instances
  - Runs on s-1vcpu-1gb instance
  - Manages request distribution and backend mapping
- Redis Database:
  - Redis 7 cluster
  - Single node deployment (db-s-1vcpu-1gb)
  - Maintains session state and routing information
  - Accessible only within VPC
- VPC (172.16.0.0/24):
  - Securely connects all services
  - Located in NYC1 region
- Firewall Rules:
  - HTTP API access (port 8080)
  - Internal VPC communication
  - Agora RTC UDP ports (1024-65535)
  - Restricted Redis access
  - Managed outbound traffic

## Security Notes

- Remember to add your SSH keys to the droplet configurations
- All sensitive information should be stored as Pulumi secrets
- The VPC isolates the infrastructure components
- Firewall rules are configured for minimum required access

## Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a new Pull Request
