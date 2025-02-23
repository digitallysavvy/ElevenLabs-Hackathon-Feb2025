import * as pulumi from '@pulumi/pulumi'
import * as digitalocean from '@pulumi/digitalocean'
import * as docker from '@pulumi/docker'

// Get config
const config = new pulumi.Config()

// Docker build options
const dockerBuildOptions = {
  platform: 'linux/amd64', // Target for DigitalOcean droplets
  args: {
    BUILDKIT_INLINE_CACHE: '1', // Build caching enabled for faster builds
    BUILD_ARGS: '--progress=plain --no-cache',
  },
}

const registryNamespace = 'custom-agent'

// Create a container registry to store our Docker images
// This will host both the proxy router and realtime agent images
const registry = new digitalocean.ContainerRegistry('custom-agent-registry', {
  name: registryNamespace,
  subscriptionTierSlug: 'basic',
})

// Retrieve Docker credentials for the registry to enable image pushing/pulling
const registryDockerCredentials = new digitalocean.ContainerRegistryDockerCredentials('registry-credentials', {
  registryName: registry.name,
  write: true,
})

// Add helper function for credential extraction
function extractDockerAuth(dockerCreds: string, serverUrl: string): { username: string; password: string } {
  const credentials = JSON.parse(dockerCreds)
  const auth = credentials?.auths?.[serverUrl]?.auth

  if (!auth) {
    throw new Error(`Missing auth for registry ${serverUrl}`)
  }

  const decoded = Buffer.from(auth, 'base64').toString('utf-8')
  const [username, password] = decoded.split(':')

  if (!username || !password) {
    throw new Error('Invalid auth string format')
  }

  return { username, password }
}

// Updated registry credentials handling
const registryCredentials = pulumi.all([registry.serverUrl, registryDockerCredentials.dockerCredentials]).apply(([serverUrl, dockerCreds]) => {
  try {
    const { username, password } = extractDockerAuth(dockerCreds, serverUrl)
    return { server: serverUrl, username, password }
  } catch (error) {
    console.error('Failed to parse registry credentials:', error)
    throw error
  }
})

// Add debugging logs for registry credentials
registryCredentials.apply((creds) => {
  console.log('Docker Registry Configuration:')
  console.log(`Server: ${creds.server}`)
  console.log(`Username is set: ${!!creds.username}`)
  console.log(`Password length: ${creds.password?.length || 0}`)
  return creds
})

// Build and push the proxy router image
const proxyImage = new docker.Image('conversational-ai-agent-router', {
  imageName: pulumi.interpolate`${registry.serverUrl}/${registryNamespace}/conversational-ai-agent-router:latest`,
  build: {
    context: '../Agora-ElevenLabs-Router',
    dockerfile: '../Agora-ElevenLabs-Router/Dockerfile',
    ...dockerBuildOptions,
  },
  registry: {
    server: registry.serverUrl,
    username: registryCredentials.apply((creds) => creds.username),
    password: registryCredentials.apply((creds) => creds.password),
  },
})

// Build and push the realtime agent image
const agentImage = new docker.Image('realtime-agent', {
  imageName: pulumi.interpolate`${registry.serverUrl}/${registryNamespace}/realtime-agent:latest`,
  build: {
    context: '../Agora-ElevenLabs-ConvoAI-Agent',
    dockerfile: '../Agora-ElevenLabs-ConvoAI-Agent/Dockerfile',
    ...dockerBuildOptions,
  },
  registry: {
    server: registry.serverUrl,
    username: registryCredentials.apply((creds) => creds.username),
    password: registryCredentials.apply((creds) => creds.password),
  },
})

// Create a Virtual Private Cloud (VPC) to securely connect our services
const vpc = new digitalocean.Vpc('custom-agent-vpc', {
  region: 'nyc1',
  ipRange: '172.16.0.0/24',
})

// Create a Redis cluster
const redisDb = new digitalocean.DatabaseCluster('custom-agent-redis', {
  engine: 'redis',
  version: '7',
  size: 'db-s-1vcpu-1gb',
  region: 'nyc1',
  nodeCount: 1,
  privateNetworkUuid: vpc.id,
})

// Add your SSH key for ssh access to the droplets
const sshKeys = [''] // Replace with your DO SSH key fingerprint

// Helper function to create agent droplets with consistent configuration
// Each agent runs in a Docker container and handles ElevenLabs API requests
const createAgentDroplet = (name: string, index: number) => {
  return new digitalocean.Droplet(name, {
    image: 'docker-20-04',
    region: 'nyc1',
    size: 'c-4', // 4 vCPUs, 8GB RAM
    vpcUuid: vpc.id,
    sshKeys: sshKeys,
    userData: pulumi.interpolate`#!/bin/bash
set -euo pipefail

# Create a log file
exec 1>/var/log/agent-startup.log 2>&1

echo "Starting agent setup at $(date)"

# Install Docker
apt-get update
apt-get install -y apt-transport-https ca-certificates curl software-properties-common
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Start Docker service
systemctl enable docker
systemctl start docker

# Wait for Docker to be ready
for i in {1..30}; do
    if docker info >/dev/null 2>&1; then
        echo "Docker is ready"
        break
    fi
    echo "Waiting for Docker... attempt $i"
    sleep 10
done

# Login to DO Container Registry
echo "Logging into registry..."
docker login -u ${registryCredentials.username} -p ${registryCredentials.password} ${registryCredentials.server}

# Create environment file
echo "Creating environment file..."
cat > /etc/agent.env << EOL
AGORA_APP_ID=${config.requireSecret('agoraAppId')}
AGORA_APP_CERT=${config.requireSecret('agoraAppCert')}
ELEVENLABS_API_KEY=${config.requireSecret('elevenLabsApiKey')}
ELEVENLABS_AGENT_ID=${config.requireSecret('elevenLabsAgentID')}
ELEVENLABS_VOICE_ID=${config.require('elevenLabsVoiceID')}
ELEVENLABS_MODEL=${config.require('elevenLabsModel')}
SERVER_PORT=8080
WRITE_AGENT_PCM=false
WRITE_RTC_PCM=false
EOL

# Pull and run agent
echo "Pulling agent image..."
docker pull ${agentImage.imageName}

echo "Starting agent container..."
docker run -d \\
    --name agent \\
    -p 0.0.0.0:8080:8080 \\
    --env-file /etc/agent.env \\
    --restart unless-stopped \\
    ${agentImage.imageName}

echo "Setup completed at $(date)"`,
  })
}

// Create multiple agent instances for load balancing and high availability
const agents = Array.from({ length: 3 }, (_, i) => createAgentDroplet(`agent-${i + 1}`, i))

// Create the proxy router droplet that distributes requests across agents
// This acts as the entry point for all client requests
const proxyDroplet = new digitalocean.Droplet('proxy-router', {
  image: 'docker-20-04',
  region: 'nyc1',
  size: 's-1vcpu-1gb',
  vpcUuid: vpc.id,
  sshKeys: sshKeys,
  userData: pulumi
    .all([registryCredentials.username, registryCredentials.password, registryCredentials.server, proxyImage.imageName, redisDb.uri, ...agents.map((agent) => agent.ipv4AddressPrivate)])
    .apply(([username, password, server, imageName, redisUrl, ...agentIps]) => {
      const backendIps = agentIps.join(',')

      return `#!/bin/bash
        set -euo pipefail

        # Create a dedicated log file
        exec 1>/var/log/startup-script.log 2>&1

        echo "=== Starting initialization script at $(date) ==="

        # Enable error handling
        set -euo pipefail

        function log() {
            echo "[$(date)]: $1"
        }

        # Wait for Docker to be ready
        log "Waiting for Docker service to be fully available..."
        for i in {1..30}; do
            if docker info >/dev/null 2>&1; then
                log "Docker is ready"
                break
            fi
            log "Attempt $i: Docker not ready yet..."
            sleep 10
        done

        # Verify Docker is running
        if ! docker info >/dev/null 2>&1; then
            log "Docker not running, attempting install..."
            curl -fsSL https://get.docker.com -o get-docker.sh
            sh get-docker.sh
            systemctl enable docker
            systemctl start docker
        fi

        # Login to registry
        log "Logging into container registry..."
        echo "${password}" | docker login -u "${username}" --password-stdin "${server}" || {
            log "Failed to log into registry"
            exit 1
        }

        # Create environment file
        log "Creating environment file..."
        cat > /etc/proxy.env << EOL
BACKEND_IPS=${backendIps}
MAX_REQUESTS_PER_BACKEND=${config.require('maxRequestsPerBackend')}
REDIS_URL=${redisUrl}
PORT=8080
ALLOW_ORIGIN=*
MAPPING_TTL_IN_S=3600
EOL

        log "Environment file created. Contents:"
        cat /etc/proxy.env

        # Pull and run container
        log "Pulling container image: ${imageName}"
        docker pull ${imageName}

        log "Starting container..."
        docker run -d \
            --name proxy \
            -p 8080:8080 \
            --env-file /etc/proxy.env \
            --restart unless-stopped \
            ${imageName}

        # Verify container is running
        if ! docker ps | grep proxy; then
            log "Container failed to start. Docker logs:"
            docker logs proxy
            exit 1
        fi

        log "=== Initialization complete at $(date) ==="`
    }),
})

// Combine droplet IDs from both proxy and agents
const allDropletIds = pulumi.all([proxyDroplet.id, ...agents.map((agent) => agent.id)]).apply((ids) => ids.map((id) => parseInt(id)))

// Single unified firewall for all droplets
new digitalocean.Firewall('custom-agent-firewall', {
  name: 'custom-agent-firewall',
  dropletIds: allDropletIds,
  inboundRules: [
    // HTTP API access on 8080 from anywhere
    {
      protocol: 'tcp',
      portRange: '8080',
      sourceAddresses: ['0.0.0.0/0', '::/0'],
    },
    // Internal VPC access on 8080
    {
      protocol: 'tcp',
      portRange: '8080',
      sourceAddresses: [vpc.ipRange],
    },
    // Agora RTC UDP ports
    {
      protocol: 'udp',
      portRange: '1024-65535',
      sourceAddresses: ['0.0.0.0/0', '::/0'],
    },
  ],
  outboundRules: [
    // Restrict Redis access to VPC only
    {
      protocol: 'tcp',
      portRange: '6379', // Redis port
      destinationAddresses: [vpc.ipRange],
    },
    // General outbound traffic
    {
      protocol: 'tcp',
      portRange: '1-65535',
      destinationAddresses: ['0.0.0.0/0', '::/0'],
    },
    {
      protocol: 'udp',
      portRange: '1-65535',
      destinationAddresses: ['0.0.0.0/0', '::/0'],
    },
  ],
})

// Export important infrastructure information for external use
export const outputs = {
  redis: {
    uri: redisDb.uri,
    host: redisDb.host,
    port: redisDb.port,
  },
  agents: {
    ips: agents.map((agent) => agent.ipv4Address),
    privateIps: agents.map((agent) => agent.ipv4AddressPrivate),
  },
  proxy: {
    publicIp: proxyDroplet.ipv4Address,
    privateIp: proxyDroplet.ipv4AddressPrivate,
  },
  registry: {
    url: registry.serverUrl,
  },
}
