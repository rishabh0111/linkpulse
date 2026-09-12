// Package config loads runtime configuration from the environment.
//
// Everything the process needs is an environment variable, so the same image runs
// against LocalStack, real DynamoDB, k3d and EKS with no rebuild. The one knob that
// matters most is ClickShards: chaos experiment 1 flips it from 1 to 16 under identical
// load to demonstrate the write-sharding mitigation from docs/data-model.md.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Port            string
	Table           string
	Region          string
	DynamoEndpoint  string // empty means real AWS
	ClickShards     int
	ClickTTLDays    int
	ClickQueueSize  int
	ClickWorkers    int
	ShutdownGrace   time.Duration
	ReadyCacheTTL   time.Duration
	BreakReadiness  bool // set by the deliberately broken :bad image (chaos experiment 2)
	EnableDebugLeak bool // fault injection for chaos experiment 4, off by default
	Version         string
}

func Load() (Config, error) {
	c := Config{
		Port:            env("PORT", "8080"),
		Table:           env("DDB_TABLE", "linkpulse"),
		Region:          env("AWS_REGION", "ap-south-1"),
		DynamoEndpoint:  env("DDB_ENDPOINT", ""),
		Version:         env("LINKPULSE_VERSION", "dev"),
		BreakReadiness:  boolEnv("LINKPULSE_BREAK_READINESS", false),
		EnableDebugLeak: boolEnv("LINKPULSE_ENABLE_DEBUG_LEAK", false),
	}

	var err error
	if c.ClickShards, err = intEnv("CLICK_SHARDS", 16); err != nil {
		return c, err
	}
	if c.ClickShards < 1 {
		return c, fmt.Errorf("CLICK_SHARDS must be >= 1, got %d", c.ClickShards)
	}
	if c.ClickTTLDays, err = intEnv("CLICK_TTL_DAYS", 30); err != nil {
		return c, err
	}
	if c.ClickQueueSize, err = intEnv("CLICK_QUEUE_SIZE", 2048); err != nil {
		return c, err
	}
	if c.ClickWorkers, err = intEnv("CLICK_WORKERS", 8); err != nil {
		return c, err
	}
	if c.ClickWorkers < 1 {
		return c, fmt.Errorf("CLICK_WORKERS must be >= 1, got %d", c.ClickWorkers)
	}
	if c.ShutdownGrace, err = durEnv("SHUTDOWN_GRACE", 15*time.Second); err != nil {
		return c, err
	}
	if c.ReadyCacheTTL, err = durEnv("READY_CACHE_TTL", 2*time.Second); err != nil {
		return c, err
	}
	return c, nil
}

func env(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}

func intEnv(k string, def int) (int, error) {
	v := strings.TrimSpace(os.Getenv(k))
	if v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def, fmt.Errorf("%s: %w", k, err)
	}
	return n, nil
}

func boolEnv(k string, def bool) bool {
	v := strings.TrimSpace(os.Getenv(k))
	if v == "" {
		return def
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return def
	}
	return b
}

func durEnv(k string, def time.Duration) (time.Duration, error) {
	v := strings.TrimSpace(os.Getenv(k))
	if v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return def, fmt.Errorf("%s: %w", k, err)
	}
	return d, nil
}
