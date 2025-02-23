# Data Storage Structure

This document outlines the data storage structure used in our Redis database for managing client-backend associations and backend status.

## Redis Key-Value Pairs

### Client-Backend Association

- **Key Format**: `client:{clientID}`
- **Value**: Backend IP address
- **Expiration**: 24 hours
- **Purpose**: Maps a client ID to its assigned backend server

Example:

```
Key: client:550e8400-e29b-41d4-a716-446655440000
Value: 192.168.1.100
```

### Backend Status

- **Key Format**: `backend:{backendIP}`
- **Value**: JSON string representing a `BackendStatus` struct
- **Expiration**: None (persists until explicitly removed or updated)
- **Purpose**: Stores the current status of each backend server

Example:

```
Key: backend:192.168.1.100
Value: {"ActiveRequests": 3, "Clients": ["550e8400-e29b-41d4-a716-446655440000", "7c9e6679-7425-40de-944b-e07fc1f90ae7"]}
```

## BackendStatus Struct

The `BackendStatus` struct contains the following fields:

1. `ActiveRequests` (int): The number of active requests currently being handled by this backend.
2. `Clients` ([]string): A list of client IDs currently connected to this backend.

## Usage in Code

The Redis storage is primarily managed in the `proxy_service/utils.go` file:

- `getOrAssignBackend`: Retrieves or assigns a backend for a client.
- `incrementActiveRequests`: Increases the active request count for a backend and adds the client to the list.
- `decrementActiveRequests`: Decreases the active request count for a backend and removes the client from the list.

These functions use Redis transactions to ensure data consistency when updating backend status.

## Purpose

This data storage structure allows the application to:

1. Maintain consistent client-backend associations.
2. Track the load on each backend server.
3. Implement load balancing by selecting the least loaded backend for new clients.
4. Manage backend capacity by tracking active requests and connected clients.
