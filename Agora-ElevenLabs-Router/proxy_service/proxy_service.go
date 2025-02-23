package proxy_service

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"time"

	"github.com/digitallysavvy/conversational-ai-agent-router/http_headers"
	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
)

// BackendStatus represents the current status of a backend server
type BackendStatus struct {
	ActiveRequests int      // Number of active requests being processed
	Clients        []string // List of client IDs connected to this backend
}

// ProxyService handles the routing and load balancing of requests to backend servers
type ProxyService struct {
	RedisClient           *redis.Client // Redis client for storing and retrieving backend status
	BackendIPs            []string      // List of backend server IP addresses
	MaxRequestsPerBackend int           // Maximum number of concurrent requests allowed per backend
	MappingTTL            time.Duration // Time to live for mapping between client and backend
	AllowOrigin           string        // Allowed origin for CORS
}

// NewProxyService creates and initializes a new ProxyService instance
func NewProxyService(
	redisClient *redis.Client,
	backendIPs []string,
	maxRequestsPerBackend int,
	mappingTTL time.Duration,
	allowOrigin string,
) *ProxyService {
	return &ProxyService{
		RedisClient:           redisClient,
		BackendIPs:            backendIPs,
		MaxRequestsPerBackend: maxRequestsPerBackend,
		MappingTTL:            mappingTTL,
		AllowOrigin:           allowOrigin,
	}
}

// RegisterRoutes sets up the HTTP routes for the proxy service
func (ps *ProxyService) RegisterRoutes(router *gin.Engine) {
	// Create a new instance of HttpHeaders
	headers := http_headers.NewHttpHeaders(ps.AllowOrigin) // set from env

	// Apply CORS middleware
	router.Use(headers.CORShttpHeaders())
	router.Use(headers.NoCache())
	router.Use(headers.Timestamp())

	// Register routes within the CORS-enabled group
	router.POST("/start_agent", ps.validateAndHandleStart())
	router.POST("/stop_agent", ps.validateAndHandleStop())
	router.GET("/health", ps.healthCheck)
}

func (ps *ProxyService) InitStaleMappingsCleaner(ctx context.Context) {
	go func(ctx context.Context) {
		log.Printf("starting stale mapping cleaner")
		ticker := time.NewTicker(5 * time.Minute)
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				log.Printf("running stale mapping cleaner")
				pipe := ps.RedisClient.Pipeline()
				max := time.Now().Add(-ps.MappingTTL)
				var min int64 = 0
				min_ := strconv.FormatInt(int64(min), 10)
				max_ := strconv.FormatInt(max.UnixMilli(), 10)
				for _, ip := range ps.BackendIPs {
					log.Printf("piping stale mapping cleaner backend: %s", ip)
					pipe.ZRemRangeByScore(ctx, fmt.Sprintf("backend:%s", ip), min_, max_)
				}
				log.Printf("exec pipe")
				pipeCmds, err := pipe.Exec(ctx)
				if err != nil {
					log.Printf("ERROR: could not delete stale mappings. pipe failed. err: %s", err)
				}
				for i, e := range pipeCmds {
					if e.Err() != nil {
						log.Printf("ERROR: could not delete stale mappings. pipe failed %d. args: %v err: %s", i, e.Args(), err)
					}
				}
			}
		}
	}(ctx)
}

// validateAndHandleStart validates the start agent request and processes it
func (ps *ProxyService) validateAndHandleStart() gin.HandlerFunc {
	return func(c *gin.Context) {
		var req AgentRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			log.Printf("ERROR: failed to parse /start_agent request body. err: %v", err)
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body"})
			return
		}

		// Validate the request
		if err := req.Validate(); err != nil {
			log.Printf("ERROR: failed to validate /start_agent request body. err: %v", err)
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		// Get the client ID and handle the start request
		clientID := ps.getClientID(c)
		ps.HandleStart(c, req, clientID)
	}
}

// validateAndHandleStop validates the stop agent request and processes it
func (ps *ProxyService) validateAndHandleStop() gin.HandlerFunc {
	return func(c *gin.Context) {
		var req AgentRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			log.Printf("ERROR: failed to parse /stop_agent request body. err: %v", err)
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body"})
			return
		}

		// Validate the request
		if err := req.Validate(); err != nil {
			log.Printf("ERROR: failed to validate /stop_agent request body. err: %v", err)
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		// Get the client ID and handle the stop request
		clientID := ps.getClientID(c)
		ps.HandleStop(c, req, clientID)
	}
}

// healthCheck performs a health check on all backend servers
func (ps *ProxyService) healthCheck(c *gin.Context) {
	results := make(map[string]string)

	// Check the health of each backend server
	for _, backendIP := range ps.BackendIPs {
		url := "http://" + backendIP + ":8080/start_agent"
		resp, err := http.Get(url)
		if err != nil {
			results[backendIP] = fmt.Sprintf("Error: %v", err)
		} else {
			defer resp.Body.Close()
			results[backendIP] = fmt.Sprintf("Status: %s", resp.Status)
		}
	}

	// Return the health check results
	c.JSON(http.StatusOK, results)
}
