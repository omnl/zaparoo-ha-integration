package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
	_ "time/tzdata"
)

// Minutes is Monday through Sunday; zero is a blocked day, never unlimited.
type Policy struct {
	ProfileID string `json:"profileId"`
	Timezone  string `json:"timezone"`
	Minutes   [7]int `json:"minutes"`
	Enabled   bool   `json:"enabled"`
}

func (p Policy) Validate() error {
	if p.ProfileID == "" {
		return errors.New("profileId is required")
	}
	if _, err := time.LoadLocation(p.Timezone); err != nil {
		return fmt.Errorf("invalid timezone: %w", err)
	}
	for _, n := range p.Minutes {
		if n < 0 || n > 1440 {
			return errors.New("daily minutes must be between 0 and 1440")
		}
	}
	return nil
}

type Bonus struct {
	Date    string `json:"date"`
	Minutes int    `json:"minutes"`
}

type Grant struct {
	ProfileID string `json:"profileId"`
	Date      string `json:"date"`
	Minutes   int    `json:"minutes"`
}

type SavedState struct {
	Version   int                       `json:"version"`
	Policies  map[string]Policy         `json:"policies"`
	Bonuses   map[string]Bonus          `json:"bonuses"`
	Grants    map[string]Grant          `json:"grants"`
	Originals map[string]map[string]any `json:"originals"`
}

type Store struct {
	mu    sync.Mutex
	path  string
	state SavedState
}

func OpenStore(path string) (*Store, error) {
	s := &Store{path: path, state: SavedState{Version: 1, Policies: map[string]Policy{}, Bonuses: map[string]Bonus{}, Grants: map[string]Grant{}, Originals: map[string]map[string]any{}}}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	if err = json.Unmarshal(b, &s.state); err != nil {
		return nil, fmt.Errorf("invalid saved policy state: %w", err)
	}
	if s.state.Version != 1 || s.state.Policies == nil || s.state.Bonuses == nil || s.state.Grants == nil || s.state.Originals == nil {
		return nil, errors.New("unsupported or incomplete policy state")
	}
	for _, p := range s.state.Policies {
		if err := p.Validate(); err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *Store) Snapshot() SavedState {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, _ := json.Marshal(s.state)
	var copy SavedState
	_ = json.Unmarshal(b, &copy)
	return copy
}

// Persist before acknowledgement; a failed disk write leaves memory unchanged.
func (s *Store) Change(change func(*SavedState) error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, _ := json.Marshal(s.state)
	var next SavedState
	_ = json.Unmarshal(b, &next)
	if err := change(&next); err != nil {
		return err
	}
	b, err := json.MarshalIndent(next, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(s.path), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(s.path), ".policy-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(b); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Rename(name, s.path); err != nil {
		return err
	}
	s.state = next
	return nil
}

func localDate(p Policy, now time.Time) string {
	loc, _ := time.LoadLocation(p.Timezone)
	return now.In(loc).Format(time.DateOnly)
}

func allowance(p Policy, bonus Bonus, now time.Time) int {
	loc, _ := time.LoadLocation(p.Timezone)
	day := (int(now.In(loc).Weekday()) + 6) % 7
	minutes := p.Minutes[day]
	if bonus.Date == localDate(p, now) {
		minutes += bonus.Minutes
	}
	return minutes
}

func limitDuration(minutes int) string {
	if minutes == 0 {
		return "1ns"
	} // The launch hook rejects this day entirely.
	return (time.Duration(minutes) * time.Minute).String()
}

func (s *Store) AddTime(profileID, requestID string, minutes int, now time.Time) error {
	if requestID == "" || len(requestID) > 128 || minutes < 1 || minutes > 1440 {
		return errors.New("requestId and 1..1440 minutes are required")
	}
	return s.Change(func(state *SavedState) error {
		p, ok := state.Policies[profileID]
		if !ok || !p.Enabled {
			return errors.New("profile has no enabled schedule")
		}
		date := localDate(p, now)
		if grant, ok := state.Grants[requestID]; ok {
			if grant.ProfileID != profileID || grant.Minutes != minutes || grant.Date != date {
				return errors.New("requestId was already used for another grant")
			}
			return nil
		}
		b := state.Bonuses[profileID]
		if b.Date != date {
			b = Bonus{Date: date}
		}
		if b.Minutes+minutes > 1440 {
			return errors.New("bonus is capped at 1440 minutes per day")
		}
		b.Minutes += minutes
		state.Bonuses[profileID] = b
		state.Grants[requestID] = Grant{profileID, date, minutes}
		// Retain recent idempotency keys across restarts; old grants cannot apply again today.
		for id, g := range state.Grants {
			if g.Date < now.AddDate(0, 0, -8).Format(time.DateOnly) {
				delete(state.Grants, id)
			}
		}
		return nil
	})
}
