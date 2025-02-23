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

func (ps *ProxyService) HandleStop(c *gin.Context, req AgentRequest, clientID string) {
	// Retrieve the backend IP for the given client ID
	backendIP, err := ps.getClientBackend(c.Request.Context(), clientID)
	if err != nil {
		log.Printf("ERROR: failed to get client backend for /stop_agent err: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Error retrieving backend", "details": err.Error()})
		return
	}

	// Prepare the request to be sent to the backend
	backendURL := "http://" + backendIP + ":8080/stop_agent"
	reqBody, _ := json.Marshal(req)
	backendReq, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost, backendURL, bytes.NewBuffer(reqBody))
	if err != nil {
		log.Printf("ERROR: failed to create new /stop_agent request. err: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Error creating request", "details": err.Error()})
		return
	}

	// Set the content type for the backend request
	backendReq.Header.Set("Content-Type", "application/json")

	// Send the request to the backend with a timeout
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(backendReq)
	if err != nil {
		log.Printf("ERROR: failed to do /stop_agent request. err: %v", err)
		c.JSON(http.StatusBadGateway, gin.H{"error": "Failed to reach backend service", "details": err.Error()})
		return
	}
	defer resp.Body.Close()

	// Read the response body from the backend
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Printf("ERROR: failed to read /stop_agent response body. err: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Error reading response body", "details": err.Error()})
		return
	}

	// Parse the JSON response from the backend
	var responseData map[string]interface{}
	if err := json.Unmarshal(respBody, &responseData); err != nil {
		log.Printf("ERROR: failed to parse /stop_agent response body. err: %v", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "Error parsing response body", "details": err.Error()})
		return
	}

	// Add the clientID to the response data
	responseData["clientID"] = clientID

	// Send the modified response back to the client
	log.Printf("sending stop response: %#v status: %d", responseData, resp.StatusCode)
	c.JSON(resp.StatusCode, responseData)

	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		err = ps.RemoveActiveRequestFromBackend(c.Request.Context(), backendIP, clientID)
		if err != nil {
			log.Printf("ERROR: remove active request from backend failed. err: %v", err)
			return
		}
	}

	// Log the completion of the stop request
	log.Printf("Stop request to backend %s clientID %s completed. Status: %d", backendURL, clientID, resp.StatusCode)
}
