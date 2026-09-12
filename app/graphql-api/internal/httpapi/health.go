package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"linkpulse/internal/metrics"
)

// handleLive is the liveness probe. It answers "is this process wedged" and nothing else.
//
// It deliberately does NOT check DynamoDB. If liveness depended on a downstream
// dependency, chaos experiment 3 (revoke the IAM policy) would restart every pod in a
// loop instead of holding them up and failing readiness — turning a dependency outage
// into a self-inflicted CrashLoopBackOff. Liveness checks the process; readiness checks
// its ability to serve.
func (s *Server) handleLive(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":  "ok",
		"version": s.cfg.Version,
	})
}

// handleReady is the readiness probe: can this pod serve traffic right now.
func (s *Server) handleReady(w http.ResponseWriter, r *http.Request) {
	// The deliberately broken image (chaos experiment 2) fails here and only here.
	// Liveness still passes, which is what makes the rollout stall rather than
	// crash-loop — the exact signature of the most common real bad deploy.
	if s.cfg.BreakReadiness {
		metrics.Ready.Set(0)
		writeJSONError(w, http.StatusServiceUnavailable, "readiness deliberately broken (LINKPULSE_BREAK_READINESS)")
		return
	}

	if err := s.checkReady(r.Context()); err != nil {
		metrics.Ready.Set(0)
		s.log.Warn("readiness failed", "err", err)
		writeJSONError(w, http.StatusServiceUnavailable, "dependency unavailable: "+err.Error())
		return
	}

	metrics.Ready.Set(1)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status":      "ready",
		"version":     s.cfg.Version,
		"clickShards": s.cfg.ClickShards,
		"queueDepth":  s.recorder.Depth(),
	})
}

// checkReady pings the store, caching the outcome for ReadyCacheTTL.
func (s *Server) checkReady(ctx context.Context) error {
	s.readyMu.Lock()
	defer s.readyMu.Unlock()

	if s.readyOnce && time.Since(s.readyAt) < s.cfg.ReadyCacheTTL {
		return s.readyErr
	}

	// Bounded independently of the caller: the kubelet's own probe timeout would
	// otherwise decide how long a hung dependency holds this lock.
	pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()

	s.readyErr = s.store.Ping(pingCtx)
	s.readyAt = time.Now()
	s.readyOnce = true
	return s.readyErr
}

// handleLeak allocates and retains memory on demand. It is fault injection for chaos
// experiment 4 (resource exhaustion → OOMKilled → CrashLoopBackOff) and is only routed
// when LINKPULSE_ENABLE_DEBUG_LEAK is true.
//
// Why this exists rather than "just set a tight memory limit and apply load": the Go
// runtime's GC keeps steady-state usage of this service flat under k6, so load alone does
// not reliably cross a limit. An experiment that only sometimes triggers cannot carry a
// CI assertion. This makes the exhaustion deterministic, and the experiment documents
// that the fault is injected — the OOMKill, the restart, the CrashLoopBackOff and the
// alert that follows are all genuine.
func (s *Server) handleLeak(w http.ResponseWriter, r *http.Request) {
	mb, err := strconv.Atoi(r.URL.Query().Get("mb"))
	if err != nil || mb <= 0 || mb > 4096 {
		writeJSONError(w, http.StatusBadRequest, "mb must be between 1 and 4096")
		return
	}

	block := make([]byte, mb<<20)
	// Touch every page: Go would otherwise hand back lazily-mapped pages that never
	// count against the container's memory limit, and nothing would be OOMKilled.
	for i := 0; i < len(block); i += 4096 {
		block[i] = 1
	}

	s.leakMu.Lock()
	s.leaked = append(s.leaked, block)
	total := 0
	for _, b := range s.leaked {
		total += len(b)
	}
	s.leakMu.Unlock()

	s.log.Warn("debug leak allocated", "mb", mb, "total_mb", total>>20)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"leakedMB": total >> 20})
}
