package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func testPolicy() Policy {
	return Policy{"child", "Europe/Zurich", [7]int{60, 60, 90, 60, 90, 120, 120}, true}
}

func TestLocalDayAndBonusExpiry(t *testing.T) {
	p := testPolicy()
	for _, test := range []struct {
		stamp string
		bonus Bonus
		want  int
	}{
		{"2026-10-04T21:59:59Z", Bonus{"2026-10-04", 30}, 150},
		{"2026-10-04T22:00:00Z", Bonus{"2026-10-04", 30}, 60},
		{"2026-10-07T12:00:00Z", Bonus{}, 90},
		{"2026-10-25T22:59:59Z", Bonus{"2026-10-25", 15}, 135},
		{"2026-10-25T23:00:00Z", Bonus{"2026-10-25", 15}, 60},
	} {
		now, _ := time.Parse(time.RFC3339, test.stamp)
		if got := allowance(p, test.bonus, now); got != test.want {
			t.Errorf("%s: got %d, want %d", test.stamp, got, test.want)
		}
	}
	if limitDuration(0) == "0" || limitDuration(0) == "0s" {
		t.Fatal("blocked days must never become unlimited Core limits")
	}
}

func TestStoreRestartAndIdempotentBonus(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	s, err := OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	p := testPolicy()
	if err = s.Change(func(state *SavedState) error { state.Policies[p.ProfileID] = p; return nil }); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 10, 6, 15, 0, 0, 0, time.UTC)
	if err = s.AddTime("child", "grant-1", 30, now); err != nil {
		t.Fatal(err)
	}
	s, err = OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = s.AddTime("child", "grant-1", 30, now); err != nil {
		t.Fatal(err)
	}
	if got := s.Snapshot().Bonuses["child"].Minutes; got != 30 {
		t.Fatalf("replayed grant doubled: %d", got)
	}
	if err = s.AddTime("child", "grant-1", 60, now); err == nil {
		t.Fatal("conflicting idempotency key accepted")
	}
	if err = s.AddTime("child", "tomorrow", 15, now.AddDate(0, 0, 1)); err != nil {
		t.Fatal(err)
	}
	if got := s.Snapshot().Bonuses["child"].Minutes; got != 15 {
		t.Fatalf("yesterday's bonus survived: %d", got)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatalf("state permissions: %o", info.Mode().Perm())
	}
}

func TestConcurrentGrantsAndFailedPersistence(t *testing.T) {
	s, _ := OpenStore(filepath.Join(t.TempDir(), "state.json"))
	p := testPolicy()
	_ = s.Change(func(state *SavedState) error { state.Policies[p.ProfileID] = p; return nil })
	var wg sync.WaitGroup
	for i := range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := s.AddTime("child", fmt.Sprint(i), 1, time.Now()); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if got := s.Snapshot().Bonuses["child"].Minutes; got != 20 {
		t.Fatalf("lost concurrent grants: %d", got)
	}
	s.path = t.TempDir() // Replacing a directory with the state file must fail.
	if err := s.AddTime("child", "failed", 15, time.Now()); err == nil {
		t.Fatal("disk failure was acknowledged")
	}
	if got := s.Snapshot().Bonuses["child"].Minutes; got != 20 {
		t.Fatalf("memory changed after disk failure: %d", got)
	}
}

func TestPolicyValidation(t *testing.T) {
	p := testPolicy()
	p.Minutes[0] = -1
	if p.Validate() == nil {
		t.Fatal("negative limit accepted")
	}
	p = testPolicy()
	p.Timezone = "Invalid/Timezone"
	if p.Validate() == nil {
		t.Fatal("invalid timezone accepted")
	}
}
