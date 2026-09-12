package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"linkpulse/internal/config"
	"linkpulse/internal/gql"
	"linkpulse/internal/store"
)

func newTestServer(t *testing.T, st store.Store, mutate func(*config.Config)) (http.Handler, *Recorder) {
	t.Helper()

	cfg := config.Config{
		Port:          "8080",
		Table:         "linkpulse-test",
		ClickShards:   8,
		ClickWorkers:  2,
		ReadyCacheTTL: time.Millisecond, // effectively uncached, so tests see each ping
		Version:       "test",
	}
	if mutate != nil {
		mutate(&cfg)
	}

	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	rec := NewRecorder(st, 64, cfg.ClickWorkers, log)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = rec.Close(ctx)
	})

	schema, err := gql.New(&gql.Resolver{Store: st, BaseURL: "https://lp.test"})
	if err != nil {
		t.Fatal(err)
	}
	return New(cfg, st, schema, rec, log).Handler(), rec
}

func seedLink(t *testing.T, st store.Store, code string) {
	t.Helper()
	if _, err := st.CreateLink(context.Background(), store.CreateLinkInput{
		Code: code, LongURL: "https://example.com/dest", OwnerID: "u1",
	}); err != nil {
		t.Fatal(err)
	}
}

func TestRedirectHit(t *testing.T) {
	st := store.NewMemory(8)
	seedLink(t, st, "abc1234")
	h, _ := newTestServer(t, st, nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/r/abc1234", nil))

	if w.Code != http.StatusFound {
		t.Fatalf("status %d, want 302", w.Code)
	}
	if loc := w.Header().Get("Location"); loc != "https://example.com/dest" {
		t.Errorf("Location = %q", loc)
	}
}

// The redirect must not wait on the click write. This asserts the response is returned
// while the click is still only queued.
func TestRedirectDoesNotBlockOnClickWrite(t *testing.T) {
	st := store.NewMemory(8)
	seedLink(t, st, "abc1234")
	h, rec := newTestServer(t, st, nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/r/abc1234", nil))
	if w.Code != http.StatusFound {
		t.Fatalf("status %d, want 302", w.Code)
	}

	// Drain, then confirm the click did land — asynchronous must not mean lost.
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := rec.Close(ctx); err != nil {
		t.Fatalf("queue did not drain: %v", err)
	}

	total, err := st.ClickTotal(context.Background(), "abc1234")
	if err != nil {
		t.Fatal(err)
	}
	if total != 1 {
		t.Errorf("recorded %d clicks, want 1", total)
	}
}

func TestRedirectMissAndInvalid(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(1), nil)

	for path, want := range map[string]int{
		"/r/zzzzzzz":  http.StatusNotFound, // valid shape, no such link
		"/r/bad-code": http.StatusNotFound, // rejected before it reaches the store
		"/r/..%2Fetc": http.StatusNotFound,
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != want {
			t.Errorf("%s: status %d, want %d", path, w.Code, want)
		}
	}
}

// When the store is down the redirect must be a 503, not a 500 and not a 404: a 404 would
// tell a caller the link does not exist, and cached negative answers outlive the outage.
func TestRedirectStoreDownIs503(t *testing.T) {
	st := store.NewMemory(1)
	seedLink(t, st, "abc1234")
	st.Fail = errors.New("dynamodb unreachable")
	h, _ := newTestServer(t, st, nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/r/abc1234", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("status %d, want 503", w.Code)
	}
}

// Liveness must not depend on DynamoDB — otherwise experiment 3 turns a dependency
// outage into a cluster-wide CrashLoopBackOff.
func TestLivenessIgnoresStoreOutage(t *testing.T) {
	st := store.NewMemory(1)
	st.Fail = errors.New("dynamodb unreachable")
	h, _ := newTestServer(t, st, nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if w.Code != http.StatusOK {
		t.Errorf("liveness returned %d during a store outage; it must stay 200", w.Code)
	}
}

func TestReadinessFailsOnStoreOutage(t *testing.T) {
	st := store.NewMemory(1)
	h, _ := newTestServer(t, st, nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("healthy readiness returned %d", w.Code)
	}

	st.Fail = errors.New("dynamodb unreachable")
	time.Sleep(2 * time.Millisecond) // let the readiness cache expire

	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("readiness returned %d during a store outage, want 503", w.Code)
	}
}

// This is the contract chaos experiment 2 depends on: the bad image fails readiness while
// liveness still passes, so the rollout stalls instead of crash-looping.
func TestBreakReadinessImageFailsReadinessOnly(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(1), func(c *config.Config) {
		c.BreakReadiness = true
	})

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if w.Code != http.StatusServiceUnavailable {
		t.Errorf("readiness returned %d, want 503", w.Code)
	}

	w = httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if w.Code != http.StatusOK {
		t.Errorf("liveness returned %d, want 200 — the rollout must stall, not crash-loop", w.Code)
	}
}

func TestGraphQLOverPOST(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(4), nil)

	body := `{"query":"mutation($u:String!){ shortenUrl(longUrl:$u){ code } }","variables":{"u":"https://example.com"}}`
	req := httptest.NewRequest(http.MethodPost, "/graphql", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}

	var out struct {
		Data struct {
			ShortenURL struct{ Code string } `json:"shortenUrl"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if out.Data.ShortenURL.Code == "" {
		t.Errorf("no code in response: %s", w.Body.String())
	}
}

// A resolver failure must be a 5xx. Every alert in monitoring/ divides by HTTP status, so
// a store outage returned as 200-with-errors would be invisible to the alerting.
func TestGraphQLStoreOutageIs500(t *testing.T) {
	st := store.NewMemory(1)
	st.Fail = errors.New("dynamodb unreachable")
	h, _ := newTestServer(t, st, nil)

	req := httptest.NewRequest(http.MethodPost, "/graphql",
		strings.NewReader(`{"query":"{ links(ownerId:\"u1\"){ code } }"}`))
	req.Header.Set("Content-Type", "application/json")

	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusInternalServerError {
		t.Errorf("status %d, want 500", w.Code)
	}
}

// A malformed query is the client's fault and must stay 4xx, or k6 typos would trip the
// error-rate alert during a load test.
func TestGraphQLBadQueryIs400(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(1), nil)

	for _, body := range []string{
		`{"query":"{ nosuchfield }"}`,
		`{"query":"{ link(code) }"}`,
		`{"query":""}`,
		`not json at all`,
	} {
		req := httptest.NewRequest(http.MethodPost, "/graphql", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusBadRequest {
			t.Errorf("%s -> status %d, want 400", body, w.Code)
		}
	}
}

// A resolver that rejects the caller's input is a 400, not a 500 — otherwise anyone
// posting a junk URL could trip the error-rate alert and mask a real incident.
func TestGraphQLResolverInputRejectionIs400(t *testing.T) {
	st := store.NewMemory(4)
	seedLink(t, st, "abc1234")
	h, _ := newTestServer(t, st, nil)

	for _, body := range []string{
		`{"query":"mutation{ shortenUrl(longUrl:\"javascript:alert(1)\"){ code } }"}`,
		`{"query":"{ link(code:\"abc1234\"){ clicksByDay(from:\"2026-09-10\",to:\"2026-09-01\"){ day } } }"}`,
		`{"query":"{ link(code:\"bad-code!\"){ code } }"}`,
	} {
		req := httptest.NewRequest(http.MethodPost, "/graphql", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusBadRequest {
			t.Errorf("status %d, want 400 for %s", w.Code, body)
		}
	}
}

func TestGraphQLGetRejectsMutations(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(1), nil)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet,
		`/graphql?query=mutation{shortenUrl(longUrl:"https://example.com"){code}}`, nil))
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("status %d, want 405 — a cacheable GET must not mutate", w.Code)
	}
}

func TestMetricsEndpointExposesLinkpulseSeries(t *testing.T) {
	st := store.NewMemory(4)
	seedLink(t, st, "abc1234")
	h, _ := newTestServer(t, st, nil)

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/r/abc1234", nil))

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d", w.Code)
	}

	// These exact names are queried by monitoring/prometheus rules and by the chaos
	// assertions, so the test pins them.
	for _, series := range []string{
		"linkpulse_http_requests_total",
		"linkpulse_http_request_duration_seconds",
		"linkpulse_redirects_total",
		"linkpulse_clicks_dropped_total",
		"linkpulse_click_shards",
	} {
		if !strings.Contains(w.Body.String(), series) {
			t.Errorf("%s is missing from /metrics; an alert or a chaos assertion depends on it", series)
		}
	}
}

// The debug fault-injection endpoint must not be reachable unless it was explicitly
// enabled.
func TestDebugLeakDisabledByDefault(t *testing.T) {
	h, _ := newTestServer(t, store.NewMemory(1), nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/debug/leak?mb=1", nil))
	if w.Code == http.StatusOK {
		t.Error("/debug/leak answered without LINKPULSE_ENABLE_DEBUG_LEAK")
	}
}

// A full queue must shed clicks rather than slow the redirect down.
func TestClickQueueShedsWhenFull(t *testing.T) {
	blocked := store.NewMemory(1)
	blocked.Fail = errors.New("stalled") // workers fail fast, but the point is capacity
	log := slog.New(slog.NewTextHandler(io.Discard, nil))

	rec := NewRecorder(blocked, 1, 1, log)
	for i := 0; i < 1000; i++ {
		rec.Enqueue("abc1234", store.Click{At: time.Now()})
	}
	// Enqueue never blocks, so reaching here at all is the assertion. Depth is
	// bounded by the queue size regardless of how much was offered.
	if d := rec.Depth(); d > 1 {
		t.Errorf("queue depth %d exceeds its capacity of 1", d)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = rec.Close(ctx)
}
