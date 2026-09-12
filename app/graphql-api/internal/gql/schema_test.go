package gql

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/graphql-go/graphql"

	"linkpulse/internal/store"
)

func newTestSchema(t *testing.T, st store.Store) graphql.Schema {
	t.Helper()
	schema, err := New(&Resolver{Store: st, BaseURL: "https://lp.test"})
	if err != nil {
		t.Fatalf("build schema: %v", err)
	}
	return schema
}

func exec(t *testing.T, schema graphql.Schema, query string, vars map[string]any) *graphql.Result {
	t.Helper()
	return graphql.Do(graphql.Params{
		Schema:         schema,
		RequestString:  query,
		VariableValues: vars,
		Context:        context.Background(),
	})
}

func TestShortenAndResolve(t *testing.T) {
	st := store.NewMemory(8)
	schema := newTestSchema(t, st)

	res := exec(t, schema, `mutation { shortenUrl(longUrl: "https://example.com/x", ownerId: "u1") { code shortUrl longUrl ownerId active } }`, nil)
	if len(res.Errors) > 0 {
		t.Fatalf("unexpected errors: %v", res.Errors)
	}

	link := res.Data.(map[string]any)["shortenUrl"].(map[string]any)
	code := link["code"].(string)
	if code == "" {
		t.Fatal("no code returned")
	}
	if want := "https://lp.test/r/" + code; link["shortUrl"] != want {
		t.Errorf("shortUrl = %v, want %v", link["shortUrl"], want)
	}
	if link["active"] != true {
		t.Errorf("new link should be active, got %v", link["active"])
	}

	// And the code must resolve back through the query side.
	res = exec(t, schema, `query($c:String!){ link(code:$c){ longUrl totalClicks } }`, map[string]any{"c": code})
	if len(res.Errors) > 0 {
		t.Fatalf("unexpected errors: %v", res.Errors)
	}
	got := res.Data.(map[string]any)["link"].(map[string]any)
	if got["longUrl"] != "https://example.com/x" {
		t.Errorf("longUrl = %v", got["longUrl"])
	}
	if got["totalClicks"] != 0 {
		t.Errorf("a new link should have 0 clicks, got %v", got["totalClicks"])
	}
}

// A missing code is null, not an error: an unknown short code is a normal outcome, and
// making it an error would put 5xx-shaped noise into the panels the chaos assertions read.
func TestUnknownCodeResolvesToNull(t *testing.T) {
	schema := newTestSchema(t, store.NewMemory(1))
	res := exec(t, schema, `{ link(code: "zzzzzzz") { code } }`, nil)
	if len(res.Errors) > 0 {
		t.Fatalf("unexpected errors: %v", res.Errors)
	}
	if got := res.Data.(map[string]any)["link"]; got != nil {
		t.Errorf("expected null, got %v", got)
	}
}

func TestShortenRejectsDangerousURLs(t *testing.T) {
	schema := newTestSchema(t, store.NewMemory(1))
	for _, bad := range []string{
		"javascript:alert(1)",
		"data:text/html,<script>alert(1)</script>",
		"file:///etc/passwd",
		"not-a-url",
		"https://",
	} {
		res := exec(t, schema, `mutation($u:String!){ shortenUrl(longUrl:$u){ code } }`, map[string]any{"u": bad})
		if len(res.Errors) == 0 {
			t.Errorf("%q was accepted; it must be rejected", bad)
		}
	}
}

// A store outage has to surface as a resolver error carrying an original error — that is
// what handleGraphQL uses to decide 500 versus 400, and so what makes experiment 3's
// error-rate assertion fire.
func TestStoreFailureSurfacesAsResolverError(t *testing.T) {
	st := store.NewMemory(1)
	st.Fail = errors.New("dynamodb unreachable")
	schema := newTestSchema(t, st)

	res := exec(t, schema, `{ links(ownerId: "u1") { code } }`, nil)
	if len(res.Errors) == 0 {
		t.Fatal("expected an error when the store is down")
	}
	if res.Errors[0].OriginalError() == nil {
		t.Error("resolver error must carry an original error so it is classified as 5xx")
	}
}

func TestClicksByDayValidatesRange(t *testing.T) {
	st := store.NewMemory(4)
	if _, err := st.CreateLink(context.Background(), store.CreateLinkInput{
		Code: "abc1234", LongURL: "https://example.com", OwnerID: "u1",
	}); err != nil {
		t.Fatal(err)
	}
	schema := newTestSchema(t, st)

	res := exec(t, schema, `{ link(code:"abc1234"){ clicksByDay(from:"2026-09-10", to:"2026-09-01"){ day } } }`, nil)
	if len(res.Errors) == 0 {
		t.Error("an inverted date range must be rejected")
	}

	res = exec(t, schema, `{ link(code:"abc1234"){ clicksByDay(from:"10-09-2026", to:"2026-09-11"){ day } } }`, nil)
	if len(res.Errors) == 0 {
		t.Error("a malformed date must be rejected")
	}
}

// The schema must not grow a subscription root by accident — the decision to cut
// subscriptions is load-bearing for the HPA and drain story.
func TestNoSubscriptionRoot(t *testing.T) {
	schema := newTestSchema(t, store.NewMemory(1))
	if schema.SubscriptionType() != nil {
		t.Error("subscriptions were cut by decision; see docs/architecture.md")
	}
}

// Every time field in the schema must serialise as RFC3339. graphql-go's default resolver
// renders a time.Time with Go's native layout ("2026-09-10 17:45:19.5 +0000 UTC"), which
// no client can parse — and it did exactly that for Click.at until this test existed.
func TestTimestampsAreRFC3339(t *testing.T) {
	st := store.NewMemory(4)
	ctx := context.Background()
	if _, err := st.CreateLink(ctx, store.CreateLinkInput{
		Code: "abc1234", LongURL: "https://example.com", OwnerID: "u1",
	}); err != nil {
		t.Fatal(err)
	}
	if err := st.RecordClick(ctx, "abc1234", store.Click{At: time.Now().UTC()}); err != nil {
		t.Fatal(err)
	}

	schema := newTestSchema(t, st)
	res := exec(t, schema, `{ link(code:"abc1234"){ createdAt recentClicks(limit:1){ at } } }`, nil)
	if len(res.Errors) > 0 {
		t.Fatalf("unexpected errors: %v", res.Errors)
	}

	link := res.Data.(map[string]any)["link"].(map[string]any)
	stamps := map[string]string{
		"link.createdAt": link["createdAt"].(string),
	}
	clicks := link["recentClicks"].([]any)
	if len(clicks) != 1 {
		t.Fatalf("expected 1 click, got %d", len(clicks))
	}
	stamps["click.at"] = clicks[0].(map[string]any)["at"].(string)

	for field, value := range stamps {
		if _, err := time.Parse(time.RFC3339, value); err != nil {
			t.Errorf("%s = %q is not RFC3339: %v", field, value, err)
		}
	}
}
