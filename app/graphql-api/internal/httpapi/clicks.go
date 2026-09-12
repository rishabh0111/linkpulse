package httpapi

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"linkpulse/internal/metrics"
	"linkpulse/internal/store"
)

// Recorder writes clicks asynchronously.
//
// This is the single most important design decision on the redirect path: a click write
// costs 2 WCU (docs/data-model.md §5) and the table is provisioned at 20 WCU, so under
// load the aggregate write is the first thing to throttle. If the redirect awaited it,
// every user would eat DynamoDB's retry latency and the service would fail closed on an
// analytics problem.
//
// So the redirect returns as soon as the lookup resolves, and clicks queue. The cost of
// that choice is that saturation shows up as *dropped clicks*, not as slow redirects —
// which is why linkpulse_clicks_dropped_total exists and why the throttle alert watches it
// rather than only request latency.
type Recorder struct {
	store store.Store
	queue chan job
	wg    sync.WaitGroup
	log   *slog.Logger

	// writeTimeout bounds one click write. Without it a throttled DynamoDB call plus
	// SDK backoff can hold a worker long enough to back the queue up on its own.
	writeTimeout time.Duration

	closeOnce sync.Once
}

type job struct {
	code  string
	click store.Click
}

func NewRecorder(st store.Store, queueSize, workers int, log *slog.Logger) *Recorder {
	if queueSize < 1 {
		queueSize = 1
	}
	if workers < 1 {
		workers = 1
	}
	r := &Recorder{
		store:        st,
		queue:        make(chan job, queueSize),
		log:          log,
		writeTimeout: 5 * time.Second,
	}
	for i := 0; i < workers; i++ {
		r.wg.Add(1)
		go r.worker()
	}
	return r
}

// Enqueue never blocks. A full queue means writes are not keeping up with redirects, and
// blocking here would convert an analytics backlog into user-visible latency.
func (r *Recorder) Enqueue(code string, c store.Click) {
	select {
	case r.queue <- job{code: code, click: c}:
		metrics.ClickQueueDepth.Set(float64(len(r.queue)))
	default:
		metrics.ClicksDropped.WithLabelValues("queue_full").Inc()
	}
}

func (r *Recorder) worker() {
	defer r.wg.Done()
	for j := range r.queue {
		metrics.ClickQueueDepth.Set(float64(len(r.queue)))

		ctx, cancel := context.WithTimeout(context.Background(), r.writeTimeout)
		err := r.store.RecordClick(ctx, j.code, j.click)
		cancel()

		switch {
		case err == nil:
			metrics.ClicksRecorded.Inc()
		case store.IsThrottle(err):
			// Counted separately from other failures: during experiment 1 this is
			// the reason label that must be non-zero, and it is what distinguishes
			// "the table refused the write" from "the table was unreachable".
			metrics.ClicksDropped.WithLabelValues("throttled").Inc()
		default:
			metrics.ClicksDropped.WithLabelValues("error").Inc()
			r.log.Warn("click write failed", "code", j.code, "err", err)
		}
	}
}

// Close stops accepting work and waits for the queue to drain. This is what makes the
// drain and PodDisruptionBudget story real: a pod that is terminating finishes the clicks
// it already accepted instead of silently discarding them.
func (r *Recorder) Close(ctx context.Context) error {
	r.closeOnce.Do(func() { close(r.queue) })

	done := make(chan struct{})
	go func() {
		r.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		return nil
	case <-ctx.Done():
		// Whatever is left is genuinely lost, and it is recorded as such rather
		// than being rounded away in the shutdown log.
		remaining := len(r.queue)
		if remaining > 0 {
			metrics.ClicksDropped.WithLabelValues("shutdown").Add(float64(remaining))
		}
		return ctx.Err()
	}
}

// Depth is exposed for the readiness and debug surfaces.
func (r *Recorder) Depth() int { return len(r.queue) }
