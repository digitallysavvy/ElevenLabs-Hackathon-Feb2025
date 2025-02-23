package proxy_service

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// getClientID retrieves or generates a unique client identifier
func (ps *ProxyService) getClientID(c *gin.Context) string {
	clientID := c.GetHeader("X-Client-ID")
	if clientID == "" {
		// Generate a new UUID if no client ID is provided
		clientID = uuid.New().String()
	}
	return clientID
}

// getOrAssignBackend retrieves an existing backend or assigns a new one for a client
func (ps *ProxyService) getOrAssignBackend(ctx context.Context, clientID string) (string, error) {
	// Try to get an existing backend for the client
	backendIP, err := ps.RedisClient.Get(ctx, "client:"+clientID).Result()
	if err == redis.Nil {
		// Client not found, assign a new backend
		backendIP, err = ps.getLeastLoadedBackend(ctx)
		if err != nil {
			log.Printf("ERROR: could not get least loaded backend err: %v", err)
			return "", fmt.Errorf("error getting least loaded backend: %v", err)
		}
	}
	if err != nil {
		return "", fmt.Errorf("error getting backend: %v", err)
	}
	return backendIP, nil
}

func (ps *ProxyService) getClientBackend(ctx context.Context, clientID string) (string, error) {
	backendIP, err := ps.RedisClient.Get(ctx, "client:"+clientID).Result()
	if err != nil {
		log.Printf("ERROR: error getting client backend err: %v", err)
		return "", fmt.Errorf("error getting client backend: %v", err)
	}
	return backendIP, nil
}

// getLeastLoadedBackend finds the backend with the lowest number of active requests
func (ps *ProxyService) getLeastLoadedBackend(ctx context.Context) (string, error) {
	var leastLoadedIP string
	minRequests := ps.MaxRequestsPerBackend

	for _, backendIP := range ps.BackendIPs {
		activeRequests, err := ps.getBackendStatus(ctx, backendIP)
		if err != nil {
			continue // Skip this backend if there's an error
		}
		if int(activeRequests) < minRequests {
			minRequests = int(activeRequests)
			leastLoadedIP = backendIP
		}
	}

	if leastLoadedIP == "" {
		log.Printf("ERROR: no available backend found err")
		return "", fmt.Errorf("no available backend found")
	}

	return leastLoadedIP, nil
}

// getBackendStatus retrieves the current status of a backend from Redis
func (ps *ProxyService) getBackendStatus(ctx context.Context, backendIP string) (int64, error) {
	r1 := time.Now()
	r2 := r1.Add(-ps.MappingTTL)
	min := strconv.FormatInt(r2.UnixMilli(), 10)
	max := strconv.FormatInt(r1.UnixMilli(), 10)
	count, err := ps.RedisClient.ZCount(ctx, fmt.Sprintf("backend:%s", backendIP), min, max).Result()
	if err != nil {
		log.Printf("ERROR: could not get backend status for backend IP: %s key: %s err: %s", backendIP, fmt.Sprintf("backend:%s", backendIP), err)
		return 0, err
	}
	return count, nil
}

func (ps *ProxyService) AddActiveRequestToBackend(ctx context.Context, backendIP, clientID string) error {
	e := redis.Z{
		Score:  float64(time.Now().UnixMilli()),
		Member: clientID,
	}

	pipe := ps.RedisClient.Pipeline()
	pipe.Set(ctx, fmt.Sprintf("client:%s", clientID), backendIP, ps.MappingTTL)
	pipe.ZAdd(ctx, fmt.Sprintf("backend:%s", backendIP), e)
	pipeCmds, err := pipe.Exec(ctx)
	if err != nil {
		log.Printf("ERROR: could not add|update client and mapping backend. pipe failed. backend %s client %s err: %s", backendIP, clientID, err)
		return err
	}
	if pipeCmds[0].Err() != nil {
		log.Printf("ERROR: could not add|update client mapping. backend %s client %s err: %s", backendIP, clientID, err)
		return err
	}
	if pipeCmds[1].Err() != nil {
		log.Printf("ERROR: could not add|update backend mapping. backend %s client %s err: %s", backendIP, clientID, err)
		return err
	}

	return nil
}

func (ps *ProxyService) RemoveActiveRequestFromBackend(ctx context.Context, backendIP, clientID string) error {
	err := ps.RedisClient.ZRem(ctx, fmt.Sprintf("backend:%s", backendIP), clientID).Err()
	if err != nil {
		log.Printf("ERROR: could not delete backend mapping backend %s client %s err: %s", backendIP, clientID, err)
		return err
	}

	return nil
}

// AgentRequest represents the structure of an agent request
type AgentRequest struct {
	ChannelName string `json:"channel_name" binding:"required"`
	UID         int    `json:"uid"          binding:"required"`
}

// Validate checks if the AgentRequest fields are valid
func (r *AgentRequest) Validate() error {
	if r.ChannelName == "" {
		return errors.New("channel_name is required")
	}
	return nil
}
