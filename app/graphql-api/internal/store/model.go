// Package store is the implementation of docs/data-model.md. The key construction in
// keys.go and the query shapes in dynamo.go correspond one-to-one with the access
// patterns A1..A7 documented there; if you change one, change the document.
package store

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrNotFound is returned when a code does not resolve (A1).
	ErrNotFound = errors.New("store: not found")
	// ErrCodeTaken is returned when the conditional put in A2 loses a race.
	ErrCodeTaken = errors.New("store: code already taken")
)

type Link struct {
	Code      string    `json:"code"`
	LongURL   string    `json:"longUrl"`
	OwnerID   string    `json:"ownerId"`
	CreatedAt time.Time `json:"createdAt"`
	Active    bool      `json:"active"`
}

type Click struct {
	At       time.Time `json:"at"`
	Referrer string    `json:"referrer"`
	Country  string    `json:"country"`
	UAClass  string    `json:"uaClass"`
}

// DayCount is one row of A5, with shards already summed.
type DayCount struct {
	Day   string `json:"day"` // YYYY-MM-DD
	Count int64  `json:"count"`
}

type CreateLinkInput struct {
	Code    string
	LongURL string
	OwnerID string
}

// Store is the whole data access surface. The interface exists so the HTTP and GraphQL
// layers can be tested against Memory without a container, and so the chaos experiments
// can reason about exactly which calls touch DynamoDB.
type Store interface {
	CreateLink(ctx context.Context, in CreateLinkInput) (Link, error)            // A2
	GetLink(ctx context.Context, code string) (Link, error)                      // A1
	RecordClick(ctx context.Context, code string, c Click) error                 // A3
	ClickTotal(ctx context.Context, code string) (int64, error)                  // A4
	ClicksByDay(ctx context.Context, code, from, to string) ([]DayCount, error)  // A5
	RecentClicks(ctx context.Context, code string, limit int32) ([]Click, error) // A6
	LinksByOwner(ctx context.Context, owner string, limit int32) ([]Link, error) // A7
	Ping(ctx context.Context) error                                              // readiness
}
