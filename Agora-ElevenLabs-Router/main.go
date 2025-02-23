package main

import (
	"context"
	"crypto/tls"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
	"github.com/redis/go-redis/v9"

	"github.com/digitallysavvy/conversational-ai-agent-router/proxy_service"
)

// Global variables to store configuration
var (
	backendIPs            []string
	maxRequestsPerBackend int
	publicServerPort      string
	redisURL              string
	mappingTTL            time.Duration
	allowOrigin           string
)

func loadEnvironmentVariables() {
	// Load .env file for local development
	err := godotenv.Load()
	if err != nil {
		log.Println("Error loading .env file:", err)
		// Not fatal as .env might not exist in all environments
	}

	// Load and validate backend IPs
	backendIPsStr := os.Getenv("BACKEND_IPS")
	if backendIPsStr != "" {
		backendIPs = strings.Split(backendIPsStr, ",")
	} else {
		log.Fatal("BACKEND_IPS environment variable is not set")
	}

	// Load and parse max requests per backend
	maxRequestsStr := os.Getenv("MAX_REQUESTS_PER_BACKEND")
	if maxRequestsStr != "" {
		var err error
		maxRequestsPerBackend, err = strconv.Atoi(maxRequestsStr)
		if err != nil {
			log.Fatalf("Invalid MAX_REQUESTS_PER_BACKEND value: %v", err)
		}
	} else {
		log.Fatal("MAX_REQUESTS_PER_BACKEND environment variable is not set")
	}

	// Load Redis URL
	redisURL = os.Getenv("REDIS_URL")
	if redisURL == "" {
		log.Fatal("REDIS_URL environment variable is not set")
	}

	// Load or set default public server port
	publicServerPort = os.Getenv("PORT")
	if publicServerPort == "" {
		publicServerPort = "8080"
		log.Println("PORT environment variable is not set")
		log.Printf("Defaulting to PORT: %s", publicServerPort)
	}

	mappingTTLEnv := os.Getenv("MAPPING_TTL_IN_S")
	if mappingTTLEnv == "" {
		mappingTTL = time.Hour
	} else {
		ttlH, err := strconv.Atoi(mappingTTLEnv)
		if err != nil {
			log.Fatalf("could not read mapping ttl %s", err)
		}
		mappingTTL = time.Duration(ttlH) * time.Second
	}

	// Load or set default allow origin
	allowOrigin = os.Getenv("ALLOW_ORIGIN")
	if allowOrigin == "" {
		allowOrigin = "*"
		log.Println("ALLOW_ORIGIN environment variable is not set")
		log.Printf("Defaulting to ALLOW_ORIGIN: %s", allowOrigin)
	}

}

func initRedisClient() *redis.Client {
	log.Println("Initializing Redis client")
	// For debugging, log the Redis URL
	log.Printf("Redis URL: %s", redisURL)

	parsedURL, err := url.Parse(redisURL)
	if err != nil {
		log.Fatalf("Failed to parse Redis URL: %v", err)
	}
	addr := parsedURL.Host

	password, ok := parsedURL.User.Password()
	if !ok {
		log.Fatalf("Failed to parse password for Redis URL: %v", err)
	}

	opts := redis.Options{
		Addr:     addr,
		Password: password,
	}

	// Enable TLS for the connection
	opts.TLSConfig = &tls.Config{
		InsecureSkipVerify: true,
	}

	log.Printf(
		"Redis options: %+v\nTLS config: %+v",
		opts,
		opts.TLSConfig,
	) // Log the parsed options

	// Create a new Redis client with the parsed options
	client := redis.NewClient(&opts)

	// Test the connection to ensure Redis is reachable
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err = client.Ping(ctx).Result()
	if err != nil {
		log.Fatalf("Failed to connect to Redis: %v", err)
	}

	log.Println("Successfully connected to Redis")
	return client
}

func setupServer() *http.Server {
	log.Println("Starting setupServer")

	redisClient := initRedisClient()
	proxyService := proxy_service.NewProxyService(
		redisClient,
		backendIPs,
		maxRequestsPerBackend,
		mappingTTL,
		allowOrigin,
	)

	router := gin.Default()

	// Register ProxyService routes
	proxyService.RegisterRoutes(router)
	proxyService.InitStaleMappingsCleaner(context.Background())

	go proxyService.StartCleanupRoutine(1 * time.Hour)
	router.GET("/ping", Ping)

	serverPort := publicServerPort
	server := &http.Server{
		Addr:    ":" + serverPort,
		Handler: router,
	}

	log.Println("Server setup completed")
	return server
}

func main() {
	loadEnvironmentVariables()
	server := setupServer()

	// Start the server in a separate goroutine to handle graceful shutdown.
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %s\n", err)
		}
	}()

	// Prepare to handle graceful shutdown.
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM)

	// Wait for a shutdown signal.
	<-quit
	log.Println("Shutting down server...")

	// Attempt to gracefully shutdown the server with a timeout of 5 seconds.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Server exiting")
}

// Ping is a handler function for the "/ping" route. It serves as a basic health check endpoint.
func Ping(c *gin.Context) {
	c.JSON(200, gin.H{
		"message": "pong",
	})
}
