package store

import (
	"context"
	"sort"
	"strings"
	"sync"
	"time"
)

// Memory is an in-process Store used by the unit tests. It exists so the GraphQL and HTTP
// layers can be tested in CI without a container, and it models the same sharded
// aggregate shape as Dynamo so a keying mistake shows up in a unit test rather than only
// under load.
type Memory struct {
	mu     sync.RWMutex
	shards int
	links  map[string]Link             // code -> link
	clicks map[string][]Click          // code -> clicks, newest appended
	stats  map[string]map[string]int64 // code -> statSK -> count
	Fail   error                       // when set, every call returns it (dependency-outage tests)
}

var _ Store = (*Memory)(nil)

func NewMemory(shards int) *Memory {
	if shards < 1 {
		shards = 1
	}
	return &Memory{
		shards: shards,
		links:  map[string]Link{},
		clicks: map[string][]Click{},
		stats:  map[string]map[string]int64{},
	}
}

func (m *Memory) Shards() int { return m.shards }

func (m *Memory) CreateLink(_ context.Context, in CreateLinkInput) (Link, error) {
	if m.Fail != nil {
		return Link{}, m.Fail
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.links[in.Code]; exists {
		return Link{}, ErrCodeTaken
	}
	l := Link{
		Code:      in.Code,
		LongURL:   in.LongURL,
		OwnerID:   in.OwnerID,
		CreatedAt: time.Now().UTC(),
		Active:    true,
	}
	m.links[in.Code] = l
	return l, nil
}

func (m *Memory) GetLink(_ context.Context, code string) (Link, error) {
	if m.Fail != nil {
		return Link{}, m.Fail
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	l, ok := m.links[code]
	if !ok {
		return Link{}, ErrNotFound
	}
	return l, nil
}

func (m *Memory) RecordClick(_ context.Context, code string, c Click) error {
	if m.Fail != nil {
		return m.Fail
	}
	if c.At.IsZero() {
		c.At = time.Now().UTC()
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.clicks[code] = append(m.clicks[code], c)
	if m.stats[code] == nil {
		m.stats[code] = map[string]int64{}
	}
	m.stats[code][statSK(dayOf(c.At), pickShard(m.shards))]++
	return nil
}

func (m *Memory) ClickTotal(ctx context.Context, code string) (int64, error) {
	days, err := m.ClicksByDay(ctx, code, "", "")
	if err != nil {
		return 0, err
	}
	var total int64
	for _, d := range days {
		total += d.Count
	}
	return total, nil
}

func (m *Memory) ClicksByDay(_ context.Context, code, from, to string) ([]DayCount, error) {
	if m.Fail != nil {
		return nil, m.Fail
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	byDay := map[string]int64{}
	for sk, count := range m.stats[code] {
		day := dayFromStatSK(sk)
		if day == "" {
			continue
		}
		if from != "" && day < from {
			continue
		}
		if to != "" && day > to {
			continue
		}
		byDay[day] += count
	}
	return sortedDays(byDay), nil
}

func (m *Memory) RecentClicks(_ context.Context, code string, limit int32) ([]Click, error) {
	if m.Fail != nil {
		return nil, m.Fail
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	all := m.clicks[code]
	out := make([]Click, 0, limit)
	for i := len(all) - 1; i >= 0 && int32(len(out)) < limit; i-- {
		out = append(out, all[i])
	}
	return out, nil
}

func (m *Memory) LinksByOwner(_ context.Context, owner string, limit int32) ([]Link, error) {
	if m.Fail != nil {
		return nil, m.Fail
	}
	if limit <= 0 || limit > 100 {
		limit = 25
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	var out []Link
	for _, l := range m.links {
		if l.OwnerID == owner {
			out = append(out, l)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.After(out[j].CreatedAt) })
	if int32(len(out)) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (m *Memory) Ping(_ context.Context) error { return m.Fail }

// ShardKeys exposes the raw aggregate keys for one link. Tests use it to assert that
// writes actually spread across shards — the property docs/data-model.md §3 depends on.
func (m *Memory) ShardKeys(code string) []string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	keys := make([]string, 0, len(m.stats[code]))
	for k := range m.stats[code] {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// sortedDays turns the shard-summed map into a chronologically ordered slice. Shared by
// both stores so they cannot disagree about ordering.
func sortedDays(byDay map[string]int64) []DayCount {
	out := make([]DayCount, 0, len(byDay))
	for day, count := range byDay {
		out = append(out, DayCount{Day: day, Count: count})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Day < out[j].Day })
	return out
}

// trimPrefix is used when reading key attributes back out of a projected GSI item.
func trimPrefix(v, prefix string) string { return strings.TrimPrefix(v, prefix) }
