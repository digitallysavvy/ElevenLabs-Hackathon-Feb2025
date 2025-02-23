package http_headers

import (
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// HttpHeaders holds configurations for handling requests, such as CORS settings.
type HttpHeaders struct {
	AllowOrigin string // List of origins allowed to access the resources.
}

// NewHttpHeaders initializes and returns a new Middleware object with specified CORS settings.
func NewHttpHeaders(allowOrigin string) *HttpHeaders {
	return &HttpHeaders{AllowOrigin: allowOrigin}
}

// NoCache sets HTTP headers to prevent client-side caching of responses.
func (m *HttpHeaders) NoCache() gin.HandlerFunc {
	return func(c *gin.Context) {
		// Set multiple cache-related headers to ensure responses are not cached.
		c.Header("Cache-Control", "private, no-cache, no-store, must-revalidate")
		c.Header("Expires", "-1")
		c.Header("Pragma", "no-cache")
	}
}

// CORShttpHeaders adds CORS (Cross-Origin Resource Sharing) headers to responses and handles pre-flight requests.
func (m *HttpHeaders) CORShttpHeaders() gin.HandlerFunc {
	return func(c *gin.Context) {
		origin := c.Request.Header.Get("Origin")
		if !m.isOriginAllowed(origin) {
			c.Header("Content-Type", "application/json")
			c.JSON(http.StatusForbidden, gin.H{
				"error": "Origin not allowed",
			})
			c.Abort()
			return
		}
		c.Header("Access-Control-Allow-Origin", origin)
		c.Header("Access-Control-Allow-Methods", "GET, POST, DELETE, PATCH, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Origin, Content-Type, X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Date, X-Api-Version, X-Client-Id, Authorization")
		c.Header("Access-Control-Allow-Credentials", "true")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}

// isOriginAllowed checks whether the provided origin is in the list of allowed origins.
func (m *HttpHeaders) isOriginAllowed(origin string) bool {
	if m.AllowOrigin == "*" {
		return true
	}

	allowedOrigins := strings.Split(m.AllowOrigin, ",")
	for _, allowed := range allowedOrigins {
		if origin == allowed {
			return true
		}
	}

	return false
}

// Timestamp adds a timestamp header to responses.
func (m *HttpHeaders) Timestamp() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Next()
		timestamp := time.Now().Format(time.RFC3339)
		c.Writer.Header().Set("X-Timestamp", timestamp)
	}
}
