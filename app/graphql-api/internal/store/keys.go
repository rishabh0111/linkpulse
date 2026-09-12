package store

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math/big"
	"time"
)

// Key prefixes. These are the literal strings in docs/data-model.md §2.
const (
	prefixLink  = "LINK#"
	prefixUser  = "USER#"
	skMeta      = "META"
	prefixClick = "CLICK#"
	prefixStat  = "STAT#"
	prefixCreat = "CREATED#"

	// dayLayout is the granularity of the CLICKSTAT aggregate.
	dayLayout = "2006-01-02"
	// tsLayout sorts lexicographically in the same order it sorts chronologically,
	// which is what lets A6 read newest-first straight off the sort key.
	tsLayout = "2006-01-02T15:04:05.000000000Z07:00"
)

func linkPK(code string) string  { return prefixLink + code }
func userPK(owner string) string { return prefixUser + owner }

// clickSK is CLICK#<ts>#<rand>. The random suffix disambiguates clicks that land in the
// same nanosecond; without it a burst would overwrite itself.
func clickSK(at time.Time) (string, error) {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("store: click suffix: %w", err)
	}
	return prefixClick + at.UTC().Format(tsLayout) + "#" + hex.EncodeToString(b[:]), nil
}

// statSK is STAT#<day>#<shard>. Shard width is fixed at 4 digits so the sort key orders
// numerically as well as lexicographically, which keeps the A5 BETWEEN range correct.
func statSK(day string, shard int) string {
	return fmt.Sprintf("%s%s#%04d", prefixStat, day, shard)
}

// statRangeLo/statRangeHi bound the A5 query. "#~" is above every digit in ASCII, so the
// upper bound covers all shards of the last day without knowing the shard count.
func statRangeLo(from string) string { return prefixStat + from + "#0000" }
func statRangeHi(to string) string   { return prefixStat + to + "#~" }

func dayOf(t time.Time) string { return t.UTC().Format(dayLayout) }

// pickShard chooses the write shard for one click. Random rather than round-robin: a
// counter would have to be shared across replicas to distribute evenly, and under the HPA
// the replica count is not fixed.
func pickShard(shards int) int {
	if shards <= 1 {
		return 0
	}
	n, err := rand.Int(rand.Reader, big.NewInt(int64(shards)))
	if err != nil {
		// Falling back to shard 0 re-creates the hot partition, so this must not be
		// silent. crypto/rand does not fail in practice; if it does, the process has
		// bigger problems than key distribution.
		return 0
	}
	return int(n.Int64())
}

// dayFromStatSK parses the day back out of a CLICKSTAT sort key.
func dayFromStatSK(sk string) string {
	if len(sk) < len(prefixStat)+len(dayLayout) {
		return ""
	}
	return sk[len(prefixStat) : len(prefixStat)+len(dayLayout)]
}
