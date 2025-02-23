package proxy_service

import (
	"bytes"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
)

func (ps *ProxyService) HandleStart(c *gin.Context, req AgentRequest, clientID string) {
	// Get or assign a backend server for this client
	backendIP, err := ps.getOrAssignBackend(c.Request.Context(), clientID)
	if err != nil {
		log.Printf("ERROR: failed to get or assign backend for /start_agent. err: %v", err)
		c.JSON(
			http.StatusInternalServerError,
			gin.H{"error": "Error assigning backend", "details": err.Error()},
		)
		return
	}

	// Prepare the request to be sent to the backend
	backendURL := "http://" + backendIP + ":8080/start_agent"
	reqBody, _ := json.Marshal(req)
	backendReq, err := http.NewRequestWithContext(
		c.Request.Context(),
		http.MethodPost,
		backendURL,
		bytes.NewBuffer(reqBody),
	)
	if err != nil {
		log.Printf("ERROR: failed to create new /start_agent request. err: %v", err)
		c.JSON(
			http.StatusInternalServerError,
			gin.H{"error": "Error creating request", "details": err.Error()},
		)
		return
	}

	backendReq.Header.Set("Content-Type", "application/json")

	// Send the request to the backend with a timeout
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(backendReq)
	if err != nil {
		log.Printf("ERROR: failed to do /start_agent request. err: %v", err)
		c.JSON(
			http.StatusBadGateway,
			gin.H{"error": "Failed to reach backend service", "details": err.Error()},
		)
		return
	}
	defer resp.Body.Close()

	// Read the response body from the backend
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("ERROR: failed to read /start_agent response body. err: %v", err)
		c.JSON(
			http.StatusInternalServerError,
			gin.H{"error": "Error reading response body", "details": err.Error()},
		)
		return
	}

	// Parse the JSON response from the backend
	var responseData map[string]interface{}
	if err := json.Unmarshal(respBody, &responseData); err != nil {
		log.Printf("ERROR: failed to parse /start_agent response body. err: %v", err)
		c.JSON(
			http.StatusInternalServerError,
			gin.H{"error": "Error parsing response body", "details": err.Error()},
		)
		return
	}

	// Add the clientID to the response data
	responseData["clientID"] = clientID

	// Send the modified response back to the client
	log.Printf("sending start response: %#v status: %d", responseData, resp.StatusCode)
	c.JSON(resp.StatusCode, responseData)

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		err = ps.AddActiveRequestToBackend(c.Request.Context(), backendIP, clientID)
		if err != nil {
			log.Printf("ERROR: adding active request to backend failed. err: %s", err)
			return
		}
	}

	// Log the request details and response status
	log.Printf("Request to backend %s clientID %s completed. Status: %d", backendURL, clientID, resp.StatusCode)
}
