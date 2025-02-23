package proxy_service

// func (ps *ProxyService) handleRenewTTL(c *gin.Context) {
// 	var req AgentRequest
// 	if err := c.ShouldBindJSON(&req); err != nil {
// 		log.Printf("ERROR: failed to parse /renew_ttl request body. err: %v", err)
// 		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request body"})
// 		return
// 	}

// 	// Validate the request
// 	if err := req.Validate(); err != nil {
// 		log.Printf("ERROR: failed to validate /renew_ttl request body. err: %v", err)
// 		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
// 		return
// 	}

// 	// Get the client ID and handle the start request
// 	clientID := ps.getClientID(c)

// 	ctx := c.Request.Context()
// 	backendIP, err := ps.getClientBackend(ctx, clientID)
// 	if err != nil {
// 		log.Printf("ERROR: failed to get client backend for /renew_ttl err: %v", err)
// 		c.JSON(http.StatusInternalServerError, gin.H{"error": "Error retrieving backend", "details": err.Error()})
// 		return
// 	}

// 	ps.AddActiveRequestToBackend(ctx, backendIP, clientID)
// }
