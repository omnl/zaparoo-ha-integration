package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

type subscriber struct {
	messages chan Message
	done     chan struct{}
	once     sync.Once
}

func (s *subscriber) close() { s.once.Do(func() { close(s.done) }) }

func (a *Agent) broadcast(msg Message) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for s := range a.clients {
		select {
		case s.messages <- msg:
		default:
			s.close()
		}
	}
}

func (a *Agent) remoteHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v0.1", a.serveWS)
	mux.HandleFunc("/v1/state", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(a.snapshot())
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+a.config.Token)) != 1 {
			w.Header().Set("WWW-Authenticate", "Bearer")
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		mux.ServeHTTP(w, r)
	})
}

func (a *Agent) serveWS(w http.ResponseWriter, r *http.Request) {
	u := websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return r.Header.Get("Origin") == "" }}
	ws, err := u.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	ws.SetReadLimit(65536)
	s := &subscriber{messages: make(chan Message, 32), done: make(chan struct{})}
	a.mu.Lock()
	a.clients[s] = struct{}{}
	a.mu.Unlock()
	defer func() { a.mu.Lock(); delete(a.clients, s); a.mu.Unlock(); s.close() }()
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	go func() {
		defer ws.Close()
		for {
			select {
			case <-s.done:
				return
			case msg := <-s.messages:
				_ = ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
				if ws.WriteJSON(msg) != nil {
					s.close()
					return
				}
			}
		}
	}()
	for {
		var request Message
		if ws.ReadJSON(&request) != nil {
			return
		}
		if len(request.ID) == 0 || request.JSONRPC != "2.0" {
			return
		}
		callCtx, done := context.WithTimeout(ctx, 20*time.Second)
		result, err := a.handleRPC(callCtx, request.Method, request.Params)
		done()
		response := Message{JSONRPC: "2.0", ID: request.ID, Result: result}
		if err != nil {
			response.Result = nil
			response.Error = &RPCError{-32000, err.Error()}
		}
		select {
		case s.messages <- response:
		case <-s.done:
			return
		}
	}
}

func (a *Agent) handleRPC(ctx context.Context, method string, params json.RawMessage) (json.RawMessage, error) {
	if method == "kidsstation.state" {
		result, _ := json.Marshal(a.snapshot())
		return result, nil
	}
	a.mu.Lock()
	ready := a.synchronized
	a.mu.Unlock()
	if !ready {
		return nil, errors.New("agent has not synchronized with Zaparoo")
	}
	switch method {
	case "kidsstation.policy.set":
		var input struct {
			ProfileID string `json:"profileId"`
			Timezone  string `json:"timezone"`
			Minutes   []int  `json:"minutes"`
			Enabled   bool   `json:"enabled"`
		}
		if err := json.Unmarshal(params, &input); err != nil {
			return nil, err
		}
		if len(input.Minutes) != 7 {
			return nil, errors.New("minutes must contain exactly seven values (Monday to Sunday)")
		}
		p := Policy{ProfileID: input.ProfileID, Timezone: input.Timezone, Enabled: input.Enabled}
		copy(p.Minutes[:], input.Minutes)
		if err := p.Validate(); err != nil {
			return nil, err
		}
		a.manage.Lock()
		a.mu.Lock()
		var list struct {
			Profiles []map[string]any `json:"profiles"`
		}
		_ = json.Unmarshal(a.state["profiles"], &list)
		a.mu.Unlock()
		var original map[string]any
		for _, profile := range list.Profiles {
			if profile["profileId"] == p.ProfileID {
				original = map[string]any{}
				for _, k := range []string{"limitsEnabled", "dailyLimit", "sessionLimit"} {
					if v, ok := profile[k]; ok {
						original[k] = v
					}
				}
			}
		}
		if original == nil {
			a.manage.Unlock()
			return nil, errors.New("unknown profileId")
		}
		err := a.store.Change(func(state *SavedState) error {
			if p.Enabled {
				if old, ok := state.Policies[p.ProfileID]; !ok || !old.Enabled {
					state.Originals[p.ProfileID] = original
				}
			}
			state.Policies[p.ProfileID] = p
			return nil
		})
		if err == nil {
			err = a.applyLocked(ctx)
		}
		a.manage.Unlock()
		a.publishState()
		if err != nil {
			return nil, err
		}
	case "kidsstation.add_time":
		var input struct {
			ProfileID string `json:"profileId"`
			Minutes   int    `json:"minutes"`
			RequestID string `json:"requestId"`
		}
		if err := json.Unmarshal(params, &input); err != nil {
			return nil, err
		}
		a.manage.Lock()
		err := a.store.AddTime(input.ProfileID, input.RequestID, input.Minutes, time.Now())
		if err == nil {
			err = a.applyLocked(ctx)
		}
		a.manage.Unlock()
		a.publishState()
		if err != nil {
			return nil, err
		}
	case "profiles", "profiles.active", "playtime", "media", "clients.current", "readers", "run", "stop", "profiles.switch":
		result, err := a.core.Call(ctx, method, params)
		if method == "profiles" && err == nil {
			result = safeProfiles(result)
		}
		return result, err
	default:
		return nil, errors.New("method is not exposed by the KidsStation agent")
	}
	result, _ := json.Marshal(a.snapshot())
	return result, nil
}

func (a *Agent) hookHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/check-launch", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		a.mu.Lock()
		ready := a.synchronized
		a.mu.Unlock()
		if !ready {
			http.Error(w, "KidsStation is synchronizing", http.StatusServiceUnavailable)
			return
		}
		active, err := a.core.Call(ctx, "profiles.active", nil)
		if err != nil {
			http.Error(w, "Zaparoo is unavailable", http.StatusServiceUnavailable)
			return
		}
		var profile struct {
			ProfileID string `json:"profileId"`
		}
		if json.Unmarshal(active, &profile) != nil {
			http.Error(w, "Invalid profile", http.StatusServiceUnavailable)
			return
		}
		state := a.store.Snapshot()
		p, managed := state.Policies[profile.ProfileID]
		if managed && p.Enabled && allowance(p, state.Bonuses[profile.ProfileID], time.Now()) == 0 {
			http.Error(w, "No media time allowed today", http.StatusForbidden)
			return
		}
		// Refresh today's policy before launch execution. Core may also have
		// checked admission earlier, before the before_media_start hook runs.
		if err := a.apply(ctx); err != nil {
			http.Error(w, "Policy could not be applied", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("ok"))
	})
	return mux
}
