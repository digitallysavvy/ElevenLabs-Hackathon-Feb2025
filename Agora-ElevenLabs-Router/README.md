# Converse Middleware Router

This project is a middleware router for Agora's Conversational Ai backend Agents. It is designed to handle incoming requests from clients, assign a backend server, and proxy the request to the appropriate backend server. It also handles rate limiting and other middleware tasks.

## Getting Started

### Prerequisites

- Go (1.21.7)
- Docker
- Docker Compose

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/digitallysavvy/conversational-ai-agent-router.git
   ```

2. Navigate to the project directory:

   ```bash
   cd conversational-ai-agent-router
   ```

3. Build the Docker image:

   ```bash
   docker build -t conversational-ai-agent-router .
   ```

4. Run the Docker container:

   ```bash
   docker run -d -p 8080:8080 conversational-ai-agent-router
   ```

### Running the Application

1. Start the Docker Compose stack:

   ```bash
   docker compose up
   ```

2. Access the application at `http://localhost:8080`.

### Environment Variables

- `BACKEND_IPS`: A comma-separated list of backend server IP addresses
- `MAX_REQUESTS_PER_BACKEND`: The maximum number of requests a backend server can handle
- `REDIS_URL`: The URL of the Redis server (with authentication)
- `PORT`: The port number on which the middleware router should listen (defaults to 8080)
- `MAPPING_TTL_IN_S`: Time-to-live for client-backend mappings in seconds (defaults to 3600)
- `ALLOW_ORIGIN`: Allowed origins for CORS (defaults to "\*")

## Testing with Curl

1. Ping Request (Wake up the server):

   ```bash
   curl -X GET http://localhost:8080/ping
   ```

2. Backend Health Check:

   ```bash
   curl -X GET http://localhost:8080/health
   ```

3. Start Agent Request:

   ```bash
   curl -X POST http://localhost:8080/start_agent \
   -H "Content-Type: application/json" \
   -d '{
     "channel_name": "test_channel",
     "uid": 123
   }'
   ```

4. Stop Agent Request:
   ```bash
   curl -X POST http://localhost:8080/stop_agent \
   -H "Content-Type: application/json" \
   -H "X-Client-ID: your_client_id_here" \
   -d '{
     "channel_name": "test_channel",
     "uid": 123
   }'
   ```

> Note: The X-Client-ID header is required for the stop_agent request and should match the client ID assigned during the start_agent request. If not provided during start_agent, a UUID will be generated.
