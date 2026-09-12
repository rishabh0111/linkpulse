// Package gql builds the GraphQL schema.
//
// The schema is constructed programmatically rather than generated from an SDL file.
// That is a deliberate choice: code generation would add a `go generate` step that CI has
// to run and verify is up to date, and the schema here is small enough that the
// generation machinery would cost more than it returns. See docs/architecture.md.
//
// There are no subscriptions. The click feed polls `recentClicks`, which is a bounded
// Query on the sort key — see docs/data-model.md A6 and the decision in the plan.
package gql

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/graphql-go/graphql"

	"linkpulse/internal/shortid"
	"linkpulse/internal/store"
)

// Resolver holds the dependencies every field needs.
type Resolver struct {
	Store   store.Store
	BaseURL string // used to render the short link, e.g. https://lp.local
}

// New builds the executable schema.
func New(r *Resolver) (graphql.Schema, error) {
	dayCount := graphql.NewObject(graphql.ObjectConfig{
		Name:        "DayCount",
		Description: "Clicks for one UTC day, with write-shards already summed.",
		Fields: graphql.Fields{
			"day":   &graphql.Field{Type: graphql.NewNonNull(graphql.String)},
			"count": &graphql.Field{Type: graphql.NewNonNull(graphql.Int)},
		},
	})

	clickType := graphql.NewObject(graphql.ObjectConfig{
		Name: "Click",
		Fields: graphql.Fields{
			"at": &graphql.Field{
				Type: graphql.NewNonNull(graphql.String),
				// Explicit, like Link.createdAt. Without a resolver the default one
				// hands graphql-go a time.Time and it renders Go's native format
				// ("2026-09-10 17:45:19.596957336 +0000 UTC"), which is not a
				// timestamp any client can parse and does not match the RFC3339 that
				// every other time field in this schema returns.
				Resolve: func(p graphql.ResolveParams) (any, error) {
					c, ok := p.Source.(store.Click)
					if !ok {
						return nil, fmt.Errorf("unexpected source type %T", p.Source)
					}
					return c.At.UTC().Format(time.RFC3339Nano), nil
				},
			},
			"referrer": &graphql.Field{Type: graphql.String},
			"country":  &graphql.Field{Type: graphql.String},
			"uaClass":  &graphql.Field{Type: graphql.String},
		},
	})

	linkType := graphql.NewObject(graphql.ObjectConfig{
		Name: "Link",
		Fields: graphql.Fields{
			"code":    &graphql.Field{Type: graphql.NewNonNull(graphql.String)},
			"longUrl": &graphql.Field{Type: graphql.NewNonNull(graphql.String)},
			"ownerId": &graphql.Field{Type: graphql.NewNonNull(graphql.String)},
			"active":  &graphql.Field{Type: graphql.NewNonNull(graphql.Boolean)},
			"createdAt": &graphql.Field{
				Type: graphql.NewNonNull(graphql.String),
				Resolve: func(p graphql.ResolveParams) (any, error) {
					l, err := linkSource(p)
					if err != nil {
						return nil, err
					}
					return l.CreatedAt.UTC().Format(time.RFC3339), nil
				},
			},
			"shortUrl": &graphql.Field{
				Type: graphql.NewNonNull(graphql.String),
				Resolve: func(p graphql.ResolveParams) (any, error) {
					l, err := linkSource(p)
					if err != nil {
						return nil, err
					}
					return strings.TrimRight(r.BaseURL, "/") + "/r/" + l.Code, nil
				},
			},
			"totalClicks": &graphql.Field{
				Type:        graphql.NewNonNull(graphql.Int),
				Description: "A4. Sums every CLICKSTAT shard for the link.",
				Resolve: func(p graphql.ResolveParams) (any, error) {
					l, err := linkSource(p)
					if err != nil {
						return nil, err
					}
					return r.Store.ClickTotal(p.Context, l.Code)
				},
			},
			"clicksByDay": &graphql.Field{
				Type:        graphql.NewNonNull(graphql.NewList(graphql.NewNonNull(dayCount))),
				Description: "A5. Defaults to the last 7 days when no range is given.",
				Args: graphql.FieldConfigArgument{
					"from": &graphql.ArgumentConfig{Type: graphql.String},
					"to":   &graphql.ArgumentConfig{Type: graphql.String},
				},
				Resolve: func(p graphql.ResolveParams) (any, error) {
					l, err := linkSource(p)
					if err != nil {
						return nil, err
					}
					from, to, err := dayRange(p.Args)
					if err != nil {
						return nil, err
					}
					return r.Store.ClicksByDay(p.Context, l.Code, from, to)
				},
			},
			"recentClicks": &graphql.Field{
				Type:        graphql.NewNonNull(graphql.NewList(graphql.NewNonNull(clickType))),
				Description: "A6. The click feed polls this; there is no subscription.",
				Args: graphql.FieldConfigArgument{
					"limit": &graphql.ArgumentConfig{
						Type:         graphql.Int,
						DefaultValue: 25,
					},
				},
				Resolve: func(p graphql.ResolveParams) (any, error) {
					l, err := linkSource(p)
					if err != nil {
						return nil, err
					}
					limit, _ := p.Args["limit"].(int)
					return r.Store.RecentClicks(p.Context, l.Code, int32(limit))
				},
			},
		},
	})

	query := graphql.NewObject(graphql.ObjectConfig{
		Name: "Query",
		Fields: graphql.Fields{
			"link": &graphql.Field{
				Type:        linkType,
				Description: "A1. Null when the code does not exist.",
				Args: graphql.FieldConfigArgument{
					"code": &graphql.ArgumentConfig{Type: graphql.NewNonNull(graphql.String)},
				},
				Resolve: func(p graphql.ResolveParams) (any, error) {
					code, _ := p.Args["code"].(string)
					if !shortid.Valid(code) {
						return nil, fmt.Errorf("%w: %q is not a valid short code", ErrInvalidInput, code)
					}
					l, err := r.Store.GetLink(p.Context, code)
					if errors.Is(err, store.ErrNotFound) {
						return nil, nil
					}
					if err != nil {
						return nil, err
					}
					return l, nil
				},
			},
			"links": &graphql.Field{
				Type:        graphql.NewNonNull(graphql.NewList(graphql.NewNonNull(linkType))),
				Description: "A7. Newest first, off the sparse GSI1.",
				Args: graphql.FieldConfigArgument{
					"ownerId": &graphql.ArgumentConfig{Type: graphql.NewNonNull(graphql.String)},
					"limit":   &graphql.ArgumentConfig{Type: graphql.Int, DefaultValue: 25},
				},
				Resolve: func(p graphql.ResolveParams) (any, error) {
					owner, _ := p.Args["ownerId"].(string)
					limit, _ := p.Args["limit"].(int)
					return r.Store.LinksByOwner(p.Context, owner, int32(limit))
				},
			},
		},
	})

	mutation := graphql.NewObject(graphql.ObjectConfig{
		Name: "Mutation",
		Fields: graphql.Fields{
			"shortenUrl": &graphql.Field{
				Type:        graphql.NewNonNull(linkType),
				Description: "A2. Generates a code and writes it conditionally.",
				Args: graphql.FieldConfigArgument{
					"longUrl": &graphql.ArgumentConfig{Type: graphql.NewNonNull(graphql.String)},
					"ownerId": &graphql.ArgumentConfig{Type: graphql.String, DefaultValue: "anonymous"},
				},
				Resolve: func(p graphql.ResolveParams) (any, error) {
					longURL, _ := p.Args["longUrl"].(string)
					owner, _ := p.Args["ownerId"].(string)
					if err := validateURL(longURL); err != nil {
						return nil, err
					}
					return createWithRetry(p.Context, r.Store, longURL, owner)
				},
			},
		},
	})

	return graphql.NewSchema(graphql.SchemaConfig{Query: query, Mutation: mutation})
}

// ErrInvalidInput marks a resolver failure that is the caller's fault rather than the
// service's. The HTTP layer maps it to 400; everything else becomes a 500.
//
// This distinction is not cosmetic. Every alert in monitoring/ and every chaos assertion
// divides by HTTP status, so if a rejected URL were counted as a server error, a client
// sending junk could trip the error-rate alert and a real incident would be
// indistinguishable from bad input.
var ErrInvalidInput = errors.New("invalid input")

// maxCodeAttempts bounds the collision retry loop. At 62^7 codes a second attempt is
// already improbable; three exists so a systematic problem fails loudly instead of
// spinning.
const maxCodeAttempts = 3

func createWithRetry(ctx context.Context, st store.Store, longURL, owner string) (store.Link, error) {
	for attempt := 0; attempt < maxCodeAttempts; attempt++ {
		code, err := shortid.New(shortid.DefaultLength)
		if err != nil {
			return store.Link{}, err
		}
		l, err := st.CreateLink(ctx, store.CreateLinkInput{
			Code:    code,
			LongURL: longURL,
			OwnerID: owner,
		})
		if errors.Is(err, store.ErrCodeTaken) {
			continue // the conditional write lost the race; try another code
		}
		if err != nil {
			return store.Link{}, err
		}
		return l, nil
	}
	return store.Link{}, fmt.Errorf("could not allocate a short code after %d attempts", maxCodeAttempts)
}

// validateURL rejects anything that is not an absolute http(s) URL. Redirecting to
// javascript: or data: would make the service an open redirect with a script payload.
func validateURL(raw string) error {
	if len(raw) > 2048 {
		return fmt.Errorf("%w: longUrl is longer than 2048 characters", ErrInvalidInput)
	}
	u, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("%w: longUrl is not a URL: %s", ErrInvalidInput, err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("%w: longUrl must be http or https, got %q", ErrInvalidInput, u.Scheme)
	}
	if u.Host == "" {
		return fmt.Errorf("%w: longUrl must include a host", ErrInvalidInput)
	}
	return nil
}

// dayRange defaults to the trailing 7 days, inclusive.
func dayRange(args map[string]any) (string, string, error) {
	const layout = "2006-01-02"
	to, _ := args["to"].(string)
	from, _ := args["from"].(string)
	now := time.Now().UTC()
	if to == "" {
		to = now.Format(layout)
	}
	if from == "" {
		from = now.AddDate(0, 0, -6).Format(layout)
	}
	for _, d := range []string{from, to} {
		if _, err := time.Parse(layout, d); err != nil {
			return "", "", fmt.Errorf("%w: dates must be YYYY-MM-DD, got %q", ErrInvalidInput, d)
		}
	}
	if from > to {
		return "", "", fmt.Errorf("%w: from (%s) is after to (%s)", ErrInvalidInput, from, to)
	}
	return from, to, nil
}

func linkSource(p graphql.ResolveParams) (store.Link, error) {
	l, ok := p.Source.(store.Link)
	if !ok {
		return store.Link{}, fmt.Errorf("unexpected source type %T", p.Source)
	}
	return l, nil
}
