package proxy_service

import (
	"context"
	"fmt"
	"time"
)

func (ps *ProxyService) StartCleanupRoutine(interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for range ticker.C {
		if err := ps.cleanupExpiredTokens(context.Background()); err != nil {
			fmt.Println("Error cleaning up tokens:", err)
		}
	}
}

func (ps *ProxyService) cleanupExpiredTokens(ctx context.Context) error {
	currentTime := float64(time.Now().Unix())
	_, err := ps.RedisClient.ZRemRangeByScore(ctx, "logout_tokens", "0", fmt.Sprintf("%f", currentTime)).Result()
	return err
}
