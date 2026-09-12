// Package shortid generates short URL codes.
//
// Codes are base62 over crypto/rand bytes. Collisions are not prevented here — they are
// caught by the conditional PutItem in the store (docs/data-model.md, A2), which is the
// only place that can be authoritative about uniqueness.
package shortid

import (
	"crypto/rand"
	"fmt"
)

const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

// DefaultLength gives 62^7 ~= 3.5e12 codes, far past anything this project will hold.
const DefaultLength = 7

// New returns a random code of n characters.
func New(n int) (string, error) {
	if n <= 0 {
		return "", fmt.Errorf("shortid: length must be positive, got %d", n)
	}
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("shortid: read random: %w", err)
	}
	out := make([]byte, n)
	for i, b := range buf {
		// Modulo bias over a 62-symbol alphabet is ~1.6% on the first two symbols.
		// Irrelevant here: uniqueness is enforced by the conditional write, not by
		// the uniformity of this distribution.
		out[i] = alphabet[int(b)%len(alphabet)]
	}
	return string(out), nil
}

// Valid reports whether s could have been produced by New — used to reject junk before
// it reaches DynamoDB and burns read capacity on the redirect path.
func Valid(s string) bool {
	if len(s) == 0 || len(s) > 32 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c >= '0' && c <= '9':
		case c >= 'A' && c <= 'Z':
		case c >= 'a' && c <= 'z':
		default:
			return false
		}
	}
	return true
}
