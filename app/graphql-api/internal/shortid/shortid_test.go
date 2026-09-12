package shortid

import "testing"

func TestNewLengthAndAlphabet(t *testing.T) {
	for _, n := range []int{1, 7, 22} {
		code, err := New(n)
		if err != nil {
			t.Fatal(err)
		}
		if len(code) != n {
			t.Errorf("length %d, want %d", len(code), n)
		}
		if !Valid(code) {
			t.Errorf("New produced a code Valid rejects: %q", code)
		}
	}
}

func TestNewRejectsNonPositiveLength(t *testing.T) {
	if _, err := New(0); err == nil {
		t.Error("expected an error for length 0")
	}
}

// Not a uniformity test — just a guard that the generator is not returning a constant,
// which would make every conditional put after the first collide.
func TestNewIsNotConstant(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		code, err := New(DefaultLength)
		if err != nil {
			t.Fatal(err)
		}
		seen[code] = true
	}
	if len(seen) < 190 {
		t.Errorf("only %d distinct codes out of 200", len(seen))
	}
}

func TestValid(t *testing.T) {
	cases := map[string]bool{
		"abc1234":                           true,
		"A":                                 true,
		"":                                  false,
		"abc-123":                           false, // path-safe but not in the alphabet
		"abc/../etc/passwd":                 false,
		"abc 123":                           false,
		"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": false, // 33 chars, over the cap
	}
	for in, want := range cases {
		if got := Valid(in); got != want {
			t.Errorf("Valid(%q) = %v, want %v", in, got, want)
		}
	}
}
