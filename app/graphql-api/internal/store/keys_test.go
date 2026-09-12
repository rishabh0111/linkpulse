package store

import (
	"context"
	"strings"
	"testing"
	"time"
)

// The A5 range query depends on shard sort keys ordering correctly as strings. If shard
// numbers were not zero-padded, STAT#day#10 would sort before STAT#day#2 and a range
// scan would silently miss shards — undercounting clicks with no error anywhere.
func TestStatSKSortsNumerically(t *testing.T) {
	prev := ""
	for shard := 0; shard < 100; shard++ {
		sk := statSK("2026-09-10", shard)
		if prev != "" && !(prev < sk) {
			t.Fatalf("shard keys out of order: %q is not < %q", prev, sk)
		}
		prev = sk
	}
}

// Every shard of the last day in the range must fall inside the BETWEEN bounds.
func TestStatRangeCoversAllShards(t *testing.T) {
	lo, hi := statRangeLo("2026-09-01"), statRangeHi("2026-09-30")
	for _, shard := range []int{0, 1, 15, 99, 9999} {
		for _, day := range []string{"2026-09-01", "2026-09-15", "2026-09-30"} {
			sk := statSK(day, shard)
			if sk < lo || sk > hi {
				t.Errorf("%q outside range [%q, %q]", sk, lo, hi)
			}
		}
	}
	// And a day outside the range must not.
	if sk := statSK("2026-10-01", 0); sk <= hi {
		t.Errorf("%q should sort above the upper bound %q", sk, hi)
	}
}

func TestDayFromStatSK(t *testing.T) {
	if got := dayFromStatSK(statSK("2026-09-10", 7)); got != "2026-09-10" {
		t.Errorf("got %q, want 2026-09-10", got)
	}
	if got := dayFromStatSK("garbage"); got != "" {
		t.Errorf("expected empty for junk input, got %q", got)
	}
}

// Click sort keys must sort chronologically as strings, since A6 reads newest-first
// straight off the sort key with no application-side sort.
func TestClickSKSortsChronologically(t *testing.T) {
	base := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	earlier, err := clickSK(base)
	if err != nil {
		t.Fatal(err)
	}
	later, err := clickSK(base.Add(time.Millisecond))
	if err != nil {
		t.Fatal(err)
	}
	if !(earlier < later) {
		t.Fatalf("%q should sort before %q", earlier, later)
	}
	if !strings.HasPrefix(earlier, prefixClick) {
		t.Errorf("missing %q prefix: %q", prefixClick, earlier)
	}
}

// Two clicks in the same instant must not collide, or a burst overwrites itself and the
// click feed silently loses rows.
func TestClickSKUniqueWithinSameInstant(t *testing.T) {
	at := time.Date(2026, 9, 10, 12, 0, 0, 0, time.UTC)
	seen := map[string]bool{}
	for i := 0; i < 500; i++ {
		sk, err := clickSK(at)
		if err != nil {
			t.Fatal(err)
		}
		if seen[sk] {
			t.Fatalf("duplicate click sort key at iteration %d: %q", i, sk)
		}
		seen[sk] = true
	}
}

// The property the whole hot-partition mitigation rests on: with sharding enabled, writes
// must actually spread over distinct sort keys. With CLICK_SHARDS=1 they must not — that
// is the configuration experiment 1 uses to reproduce the throttle.
func TestWriteShardingSpreadsAggregateKeys(t *testing.T) {
	ctx := context.Background()
	const clicks = 400

	single := NewMemory(1)
	sharded := NewMemory(16)
	for i := 0; i < clicks; i++ {
		if err := single.RecordClick(ctx, "abc", Click{}); err != nil {
			t.Fatal(err)
		}
		if err := sharded.RecordClick(ctx, "abc", Click{}); err != nil {
			t.Fatal(err)
		}
	}

	if got := len(single.ShardKeys("abc")); got != 1 {
		t.Errorf("CLICK_SHARDS=1 must write one aggregate key, got %d", got)
	}
	// 400 writes over 16 shards hitting fewer than 10 would mean the distribution is
	// broken, not unlucky.
	if got := len(sharded.ShardKeys("abc")); got < 10 {
		t.Errorf("CLICK_SHARDS=16 spread over only %d keys", got)
	}

	// Sharding must not change the answer, only where it is stored.
	for name, st := range map[string]*Memory{"single": single, "sharded": sharded} {
		total, err := st.ClickTotal(ctx, "abc")
		if err != nil {
			t.Fatal(err)
		}
		if total != clicks {
			t.Errorf("%s: total %d, want %d", name, total, clicks)
		}
	}
}

func TestClicksByDayFiltersRange(t *testing.T) {
	ctx := context.Background()
	m := NewMemory(4)
	days := []string{"2026-09-08", "2026-09-09", "2026-09-10"}
	for _, d := range days {
		at, err := time.Parse("2006-01-02", d)
		if err != nil {
			t.Fatal(err)
		}
		for i := 0; i < 3; i++ {
			if err := m.RecordClick(ctx, "abc", Click{At: at}); err != nil {
				t.Fatal(err)
			}
		}
	}

	got, err := m.ClicksByDay(ctx, "abc", "2026-09-09", "2026-09-10")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 days, got %d (%v)", len(got), got)
	}
	if got[0].Day != "2026-09-09" || got[1].Day != "2026-09-10" {
		t.Errorf("days out of order: %v", got)
	}
	for _, dc := range got {
		if dc.Count != 3 {
			t.Errorf("%s: count %d, want 3", dc.Day, dc.Count)
		}
	}
}

func TestCreateLinkRejectsDuplicateCode(t *testing.T) {
	ctx := context.Background()
	m := NewMemory(1)
	in := CreateLinkInput{Code: "abc1234", LongURL: "https://example.com", OwnerID: "u1"}
	if _, err := m.CreateLink(ctx, in); err != nil {
		t.Fatal(err)
	}
	if _, err := m.CreateLink(ctx, in); err != ErrCodeTaken {
		t.Errorf("expected ErrCodeTaken, got %v", err)
	}
}

func TestRecentClicksNewestFirst(t *testing.T) {
	ctx := context.Background()
	m := NewMemory(1)
	base := time.Date(2026, 9, 10, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 5; i++ {
		if err := m.RecordClick(ctx, "abc", Click{At: base.Add(time.Duration(i) * time.Minute)}); err != nil {
			t.Fatal(err)
		}
	}
	got, err := m.RecentClicks(ctx, "abc", 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("expected 3 clicks, got %d", len(got))
	}
	for i := 1; i < len(got); i++ {
		if !got[i-1].At.After(got[i].At) {
			t.Errorf("not newest-first: %v then %v", got[i-1].At, got[i].At)
		}
	}
}
