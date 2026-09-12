// Package httpapi is the HTTP surface: the GraphQL endpoint, the redirect hot path, the
// probes, the metrics endpoint and a minimal dashboard.
package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/graphql-go/graphql"
	"github.com/graphql-go/graphql/gqlerrors"
	"github.com/graphql-go/graphql/language/parser"
	"github.com/graphql-go/graphql/language/source"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"linkpulse/internal/config"
	"linkpulse/internal/gql"
	"linkpulse/internal/metrics"
	"linkpulse/internal/shortid"
	"linkpulse/internal/store"
)

type Server struct {
	cfg      config.Config
	store    store.Store
	schema   graphql.Schema
	recorder *Recorder
	log      *slog.Logger

	// readiness cache: /readyz is polled by the kubelet every few seconds and by
	// Prometheus blackbox checks on top of that. Without a cache the probe itself
	// becomes a meaningful share of the DescribeTable call volume.
	readyMu   sync.Mutex
	readyAt   time.Time
	readyErr  error
	readyOnce bool

	leakMu sync.Mutex
	leaked [][]byte
}

func New(cfg config.Config, st store.Store, schema graphql.Schema, rec *Recorder, log *slog.Logger) *Server {
	return &Server{cfg: cfg, store: st, schema: schema, recorder: rec, log: log}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()

	mux.Handle("POST /graphql", s.instrument("graphql", http.HandlerFunc(s.handleGraphQL)))
	mux.Handle("GET /graphql", s.instrument("graphql", http.HandlerFunc(s.handleGraphQLGet)))
	mux.Handle("GET /r/{code}", s.instrument("redirect", http.HandlerFunc(s.handleRedirect)))
	mux.Handle("GET /healthz", s.instrument("healthz", http.HandlerFunc(s.handleLive)))
	mux.Handle("GET /readyz", s.instrument("readyz", http.HandlerFunc(s.handleReady)))
	mux.Handle("GET /metrics", promhttp.Handler())
	mux.Handle("GET /", s.instrument("dashboard", http.HandlerFunc(s.handleDashboard)))

	if s.cfg.EnableDebugLeak {
		// Fault injection for chaos experiment 4, off unless explicitly enabled.
		// Deliberately not instrumented: it must not appear in the RED panels the
		// experiment is asserting against.
		s.log.Warn("debug leak endpoint is ENABLED — fault injection only, never in a real deployment")
		mux.Handle("POST /debug/leak", http.HandlerFunc(s.handleLeak))
	}

	return mux
}

// ---------- middleware ----------

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (w *statusRecorder) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

// instrument records the RED metrics. The route label is passed in rather than taken from
// the path, so a scan of random short codes cannot create unbounded label cardinality.
func (s *Server) instrument(route string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		metrics.HTTPDuration.WithLabelValues(route).Observe(time.Since(start).Seconds())
		metrics.HTTPRequests.WithLabelValues(route, r.Method, strconv.Itoa(rec.status)).Inc()
	})
}

// ---------- GraphQL ----------

type graphQLRequest struct {
	Query         string         `json:"query"`
	Variables     map[string]any `json:"variables"`
	OperationName string         `json:"operationName"`
}

func (s *Server) handleGraphQL(w http.ResponseWriter, r *http.Request) {
	// 64 KB is far more than any query this schema accepts and keeps an oversized
	// body from being read into memory at all.
	body := http.MaxBytesReader(w, r.Body, 64<<10)
	defer body.Close()

	var req graphQLRequest
	if err := json.NewDecoder(body).Decode(&req); err != nil {
		writeJSONError(w, http.StatusBadRequest, "malformed request body")
		return
	}
	if strings.TrimSpace(req.Query) == "" {
		writeJSONError(w, http.StatusBadRequest, "query is required")
		return
	}
	s.execute(w, r.Context(), req)
}

// handleGraphQLGet supports read-only queries over GET, which is what makes the k6
// analytics scenario and a curl-based smoke test straightforward. Mutations over GET are
// rejected — a GET that mutates is cacheable by anything in the path.
func (s *Server) handleGraphQLGet(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("query")
	if strings.TrimSpace(q) == "" {
		writeJSONError(w, http.StatusBadRequest, "query is required")
		return
	}
	if strings.Contains(q, "mutation") {
		writeJSONError(w, http.StatusMethodNotAllowed, "mutations must use POST")
		return
	}
	req := graphQLRequest{Query: q, OperationName: r.URL.Query().Get("operationName")}
	if raw := r.URL.Query().Get("variables"); raw != "" {
		if err := json.Unmarshal([]byte(raw), &req.Variables); err != nil {
			writeJSONError(w, http.StatusBadRequest, "variables must be JSON")
			return
		}
	}
	s.execute(w, r.Context(), req)
}

// execute runs one GraphQL request.
//
// This is graphql.Do() split into its three phases on purpose. Do() returns parse,
// validation and resolver failures in one undifferentiated Errors slice, but they mean
// completely different things to an HTTP client and to the alerting: a query that does not
// parse is the caller's fault (4xx), while a resolver that could not reach DynamoDB is
// ours (5xx). Every alert in monitoring/ and every chaos assertion divides by HTTP status,
// so collapsing the two would make bad input look like an outage — and, worse, make a real
// outage look like a 200 with an errors array that nothing is watching.
func (s *Server) execute(w http.ResponseWriter, ctx context.Context, req graphQLRequest) {
	doc, err := parser.Parse(parser.ParseParams{
		Source: source.NewSource(&source.Source{Body: []byte(req.Query), Name: "GraphQL request"}),
	})
	if err != nil {
		s.writeResult(w, http.StatusBadRequest, &graphql.Result{Errors: gqlerrors.FormatErrors(err)})
		return
	}

	if vr := graphql.ValidateDocument(&s.schema, doc, nil); !vr.IsValid {
		s.writeResult(w, http.StatusBadRequest, &graphql.Result{Errors: vr.Errors})
		return
	}

	result := graphql.Execute(graphql.ExecuteParams{
		Schema:        s.schema,
		AST:           doc,
		OperationName: req.OperationName,
		Args:          req.Variables,
		Context:       ctx,
	})

	status := http.StatusOK
	if len(result.Errors) > 0 {
		status = http.StatusInternalServerError
		if allInvalidInput(result.Errors) {
			// A resolver can also reject the caller's input — an unparseable date
			// or a javascript: URL. That is still a 400.
			status = http.StatusBadRequest
		}
		s.log.Warn("graphql resolver errors",
			"count", len(result.Errors),
			"status", status,
			"first", result.Errors[0].Message)
	}

	s.writeResult(w, status, result)
}

// allInvalidInput reports whether every error is a gql.ErrInvalidInput rejection.
// One genuine backend failure among them makes the whole response a 5xx — under-reporting
// an outage is far worse than over-reporting a bad request.
func allInvalidInput(errs []gqlerrors.FormattedError) bool {
	for _, e := range errs {
		if !isInvalidInput(e.OriginalError()) {
			return false
		}
	}
	return len(errs) > 0
}

// isInvalidInput unwraps graphql-go's error chain. The executor wraps a resolver's error
// in a *gqlerrors.Error, which does not implement Unwrap, so errors.Is alone stops at the
// wrapper and every resolver error would look like a 500.
func isInvalidInput(err error) bool {
	for i := 0; err != nil && i < 8; i++ {
		if errors.Is(err, gql.ErrInvalidInput) {
			return true
		}
		var located *gqlerrors.Error
		if !errors.As(err, &located) || located.OriginalError == nil || located.OriginalError == err {
			return false
		}
		err = located.OriginalError
	}
	return false
}

func (s *Server) writeResult(w http.ResponseWriter, status int, result *graphql.Result) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(result); err != nil {
		s.log.Error("encode graphql response", "err", err)
	}
}

// ---------- redirect hot path ----------

func (s *Server) handleRedirect(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !shortid.Valid(code) {
		metrics.Redirects.WithLabelValues("invalid").Inc()
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	link, err := s.store.GetLink(r.Context(), code)
	switch {
	case errors.Is(err, store.ErrNotFound):
		metrics.Redirects.WithLabelValues("miss").Inc()
		http.Error(w, "not found", http.StatusNotFound)
		return
	case err != nil:
		metrics.Redirects.WithLabelValues("error").Inc()
		s.log.Error("redirect lookup failed", "code", code, "err", err)
		http.Error(w, "temporarily unavailable", http.StatusServiceUnavailable)
		return
	}

	if !link.Active {
		metrics.Redirects.WithLabelValues("inactive").Inc()
		http.Error(w, "gone", http.StatusGone)
		return
	}

	// Queue the click and return. See the Recorder doc comment for why this is not
	// awaited.
	s.recorder.Enqueue(code, store.Click{
		At:       time.Now().UTC(),
		Referrer: truncate(r.Referer(), 256),
		Country:  r.Header.Get("CF-IPCountry"),
		UAClass:  classifyUA(r.UserAgent()),
	})

	metrics.Redirects.WithLabelValues("hit").Inc()
	// 302 rather than 301: a permanent redirect is cached by the browser, and a
	// cached redirect records no click at all.
	http.Redirect(w, r, link.LongURL, http.StatusFound)
}

// classifyUA buckets user agents into a handful of classes. Storing the raw string would
// put unbounded-cardinality data in the click item for no analytical gain.
func classifyUA(ua string) string {
	l := strings.ToLower(ua)
	switch {
	case l == "":
		return "unknown"
	case strings.Contains(l, "bot"), strings.Contains(l, "crawl"), strings.Contains(l, "spider"),
		strings.Contains(l, "k6"), strings.Contains(l, "curl"):
		return "bot"
	case strings.Contains(l, "android"), strings.Contains(l, "iphone"), strings.Contains(l, "mobile"):
		return "mobile"
	default:
		return "desktop"
	}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"errors": []map[string]string{{"message": msg}},
	})
}
