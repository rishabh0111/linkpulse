// Package metrics declares every series the service exposes.
//
// These names are load-bearing: monitoring/prometheus rules and the chaos assertions in
// scripts/chaos query them by name. Renaming a metric here breaks an alert and a CI gate,
// so treat this file as an interface.
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

const namespace = "linkpulse"

var (
	// --- RED metrics on the HTTP surface ---

	// Rate and Errors. The `code` label is what the error-rate alert divides by.
	HTTPRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "http_requests_total",
		Help:      "HTTP requests by route, method and status class.",
	}, []string{"route", "method", "code"})

	// Duration. Buckets are tuned for a redirect that should sit in single-digit
	// milliseconds; the top buckets exist to make throttle-induced latency visible
	// rather than clipping it into +Inf.
	HTTPDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: namespace,
		Name:      "http_request_duration_seconds",
		Help:      "HTTP request duration by route.",
		Buckets:   []float64{0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	}, []string{"route"})

	Redirects = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "redirects_total",
		Help:      "Redirect outcomes: hit, miss, inactive or error.",
	}, []string{"result"})

	// --- click pipeline ---

	ClicksRecorded = promauto.NewCounter(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "clicks_recorded_total",
		Help:      "Clicks successfully written to the aggregate.",
	})

	// ClicksDropped is the metric that tells the truth during experiment 1. The
	// redirect stays fast under throttling precisely because click writes are
	// asynchronous, so the user-visible symptom is lost analytics, not slow
	// redirects. An alert on redirect latency alone would miss the incident.
	ClicksDropped = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "clicks_dropped_total",
		Help:      "Clicks discarded without being recorded, by reason.",
	}, []string{"reason"})

	ClickQueueDepth = promauto.NewGauge(prometheus.GaugeOpts{
		Namespace: namespace,
		Name:      "click_queue_depth",
		Help:      "Clicks buffered and not yet written.",
	})

	// ClickShards publishes the CLICK_SHARDS setting so a dashboard can annotate
	// which side of the experiment-1 config flip a run is on.
	ClickShards = promauto.NewGauge(prometheus.GaugeOpts{
		Namespace: namespace,
		Name:      "click_shards",
		Help:      "Configured write-shard count for the click aggregate.",
	})

	// --- DynamoDB ---

	DynamoRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "dynamodb_requests_total",
		Help:      "DynamoDB calls by operation and result.",
	}, []string{"op", "result"})

	// DynamoThrottles is the application-side view of the same event CloudWatch
	// reports as ThrottledRequests. Experiment 1 asserts on both: this one proves the
	// application saw it, the CloudWatch one proves the table did.
	DynamoThrottles = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: namespace,
		Name:      "dynamodb_throttles_total",
		Help:      "DynamoDB calls rejected for capacity reasons.",
	}, []string{"op"})

	DynamoDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: namespace,
		Name:      "dynamodb_request_duration_seconds",
		Help:      "DynamoDB call duration by operation.",
		Buckets:   []float64{0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	}, []string{"op"})

	// --- readiness ---

	// Ready is 1 or 0. kube_pod_status_ready says what Kubernetes concluded; this says
	// what the process itself believes, and the gap between them during experiment 3
	// is the interesting part.
	Ready = promauto.NewGauge(prometheus.GaugeOpts{
		Namespace: namespace,
		Name:      "ready",
		Help:      "1 when the readiness probe is passing, 0 otherwise.",
	})

	BuildInfo = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: namespace,
		Name:      "build_info",
		Help:      "Always 1; labels carry the running version.",
	}, []string{"version"})
)

// A CounterVec exports nothing until a label combination is first observed, and
// `rate(missing_series[5m])` returns no data rather than zero. An alert written against
// an absent series therefore cannot fire, and a Grafana panel shows "No data" instead of
// a flat line — which during an incident is indistinguishable from a broken scrape.
//
// So every label value the chaos assertions and alert rules depend on is created at zero
// here, at startup. Only closed label sets are seeded: HTTP status codes and DynamoDB
// operations are open-ended and seeding them would invent cardinality.
func init() {
	for _, reason := range []string{"queue_full", "throttled", "error", "shutdown"} {
		ClicksDropped.WithLabelValues(reason).Add(0)
	}
	for _, result := range []string{"hit", "miss", "inactive", "invalid", "error"} {
		Redirects.WithLabelValues(result).Add(0)
	}
	for _, op := range []string{"GetItem", "PutItem", "UpdateItem", "Query", "DescribeTable"} {
		DynamoThrottles.WithLabelValues(op).Add(0)
	}
}
