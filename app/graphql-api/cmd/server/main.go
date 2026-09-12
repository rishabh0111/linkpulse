// Command server is the LinkPulse API.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"linkpulse/internal/config"
	"linkpulse/internal/gql"
	"linkpulse/internal/httpapi"
	"linkpulse/internal/metrics"
	"linkpulse/internal/store"
)

func main() {
	// JSON to stdout: Promtail ships the container's stdout to Loki, so structured
	// logs mean the incident timeline in docs/evidence is queryable by field rather
	// than grepped.
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: logLevel(),
	}))
	slog.SetDefault(log)

	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	metrics.BuildInfo.WithLabelValues(cfg.Version).Set(1)
	metrics.ClickShards.Set(float64(cfg.ClickShards))
	metrics.Ready.Set(0)

	log.Info("starting",
		"version", cfg.Version,
		"table", cfg.Table,
		"region", cfg.Region,
		"endpoint", endpointLabel(cfg.DynamoEndpoint),
		"click_shards", cfg.ClickShards,
		"click_workers", cfg.ClickWorkers,
	)

	// Bounded so a wrong endpoint or a missing credential chain fails the pod in
	// seconds rather than hanging the startup probe.
	initCtx, cancelInit := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancelInit()

	st, err := store.NewDynamo(initCtx, store.Options{
		Table:    cfg.Table,
		Region:   cfg.Region,
		Endpoint: cfg.DynamoEndpoint,
		Shards:   cfg.ClickShards,
		TTLDays:  cfg.ClickTTLDays,
	})
	if err != nil {
		return err
	}

	recorder := httpapi.NewRecorder(st, cfg.ClickQueueSize, cfg.ClickWorkers, log)

	schema, err := gql.New(&gql.Resolver{
		Store:   st,
		BaseURL: os.Getenv("PUBLIC_BASE_URL"),
	})
	if err != nil {
		return err
	}

	srv := &http.Server{
		Addr:    ":" + cfg.Port,
		Handler: httpapi.New(cfg, st, schema, recorder, log).Handler(),
		// A slow or absent client must not be able to hold a connection open
		// indefinitely; the redirect path has no legitimate reason to be slow.
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// SIGTERM is what Kubernetes sends before it kills a pod. Handling it is what
	// makes `kubectl drain` and the PodDisruptionBudget demo show a clean handover
	// instead of dropped requests — see docs/runbook.md.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		log.Info("listening", "addr", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		log.Info("shutdown signal received", "grace", cfg.ShutdownGrace.String())
	}

	metrics.Ready.Set(0)

	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownGrace)
	defer cancel()

	// Order matters. Stop accepting connections first, then drain the click queue:
	// draining first would let new redirects keep refilling it.
	httpErr := srv.Shutdown(shutdownCtx)
	if httpErr != nil {
		log.Warn("http shutdown incomplete", "err", httpErr)
	}
	if err := recorder.Close(shutdownCtx); err != nil {
		log.Warn("click queue did not drain", "err", err, "remaining", recorder.Depth())
	}

	log.Info("stopped")
	return nil
}

func logLevel() slog.Level {
	switch os.Getenv("LOG_LEVEL") {
	case "debug", "DEBUG":
		return slog.LevelDebug
	case "warn", "WARN":
		return slog.LevelWarn
	case "error", "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// endpointLabel keeps a custom endpoint out of the logs verbatim while still recording
// which backend the process is talking to.
func endpointLabel(ep string) string {
	if ep == "" {
		return "aws"
	}
	return ep
}
