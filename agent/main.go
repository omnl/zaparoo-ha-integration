package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

type Config struct {
	Listen           string `json:"listen"`
	HookListen       string `json:"hookListen"`
	Token            string `json:"token"`
	CoreURL          string `json:"coreUrl"`
	StateFile        string `json:"stateFile"`
	KodiURL          string `json:"kodiUrl"`
	KodiUser         string `json:"kodiUser,omitempty"`
	KodiPassword     string `json:"kodiPassword,omitempty"`
	ESURL            string `json:"esUrl"`
	RetroArchAddress string `json:"retroArchAddress"`
}

type Agent struct {
	core         *Core
	store        *Store
	config       Config
	mu           sync.Mutex
	manage       sync.Mutex
	state        map[string]json.RawMessage
	synchronized bool
	applied      map[string]string
	events       chan Message
	clients      map[*subscriber]struct{}
	notifier     *Notifier
}

func newAgent(config Config, store *Store) *Agent {
	return &Agent{core: &Core{URL: config.CoreURL}, store: store, config: config, state: map[string]json.RawMessage{}, applied: map[string]string{}, events: make(chan Message, 128), clients: map[*subscriber]struct{}{}, notifier: &Notifier{config: config, client: &http.Client{Timeout: 3 * time.Second}}}
}

func (a *Agent) snapshot() map[string]any {
	a.mu.Lock()
	defer a.mu.Unlock()
	state := map[string]any{"connected": a.synchronized, "synchronized": a.synchronized, "agentVersion": "0.1.0", "policies": a.store.Snapshot()}
	for k, v := range a.state {
		state[k] = v
	}
	return state
}

func (a *Agent) publishState() {
	data, _ := json.Marshal(a.snapshot())
	a.broadcast(Message{JSONRPC: "2.0", Method: "kidsstation.state", Params: data})
}

func (a *Agent) refresh(ctx context.Context) error {
	data := map[string]json.RawMessage{}
	for _, method := range []string{"profiles", "profiles.active", "playtime", "media", "clients.current", "readers"} {
		r, err := a.core.Call(ctx, method, nil)
		if err != nil {
			return fmt.Errorf("%s: %w", method, err)
		}
		if method == "profiles" {
			r = safeProfiles(r)
		}
		data[method] = r
	}
	var current struct {
		Capabilities []string `json:"capabilities"`
	}
	if err := json.Unmarshal(data["clients.current"], &current); err != nil {
		return err
	}
	allowed := false
	for _, capability := range current.Capabilities {
		if capability == "profiles.manage" {
			allowed = true
		}
	}
	if !allowed {
		return fmt.Errorf("local Core connection lacks profiles.manage")
	}
	a.mu.Lock()
	a.state = data
	a.mu.Unlock()
	if err := a.apply(ctx); err != nil {
		return err
	}
	a.mu.Lock()
	a.synchronized = true
	a.mu.Unlock()
	a.publishState()
	return nil
}

func safeProfiles(raw json.RawMessage) json.RawMessage {
	var profiles struct {
		Profiles []map[string]any `json:"profiles"`
	}
	if json.Unmarshal(raw, &profiles) != nil {
		return raw
	}
	for _, p := range profiles.Profiles {
		delete(p, "switchId")
	}
	result, _ := json.Marshal(profiles)
	return result
}

func (a *Agent) apply(ctx context.Context) error {
	a.manage.Lock()
	defer a.manage.Unlock()
	return a.applyLocked(ctx)
}

func (a *Agent) applyLocked(ctx context.Context) error {
	saved := a.store.Snapshot()
	for id, p := range saved.Policies {
		params := map[string]any{"profileId": id}
		if p.Enabled {
			params["limitsEnabled"] = true
			params["dailyLimit"] = limitDuration(allowance(p, saved.Bonuses[id], time.Now()))
		} else {
			original, ok := saved.Originals[id]
			if !ok {
				continue
			}
			params["clearLimits"] = true
			for k, v := range original {
				params[k] = v
			}
		}
		encoded, _ := json.Marshal(params)
		if a.applied[id] == string(encoded) {
			continue
		}
		result, err := a.core.Call(ctx, "profiles.update", params)
		if err != nil {
			return err
		}
		a.applied[id] = string(encoded)
		// Keep the profile snapshot current immediately after an acknowledged write.
		a.mu.Lock()
		var list struct {
			Profiles []map[string]any `json:"profiles"`
		}
		_ = json.Unmarshal(a.state["profiles"], &list)
		var updated map[string]any
		_ = json.Unmarshal(result, &updated)
		delete(updated, "switchId")
		for i, old := range list.Profiles {
			if old["profileId"] == id {
				list.Profiles[i] = updated
			}
		}
		a.state["profiles"], _ = json.Marshal(list)
		a.mu.Unlock()
	}
	return nil
}

func (a *Agent) Run(ctx context.Context) {
	go a.core.Run(ctx, func(ctx context.Context) error {
		a.manage.Lock()
		a.applied = map[string]string{}
		a.manage.Unlock()
		return a.refresh(ctx)
	}, func(msg Message) {
		select {
		case a.events <- msg:
		default:
			log.Print("Core events overflow; next snapshot restores current state")
		}
	}, func() { a.mu.Lock(); a.synchronized = false; a.mu.Unlock(); a.publishState() })
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	live := time.NewTicker(15 * time.Second)
	defer live.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case msg := <-a.events:
			a.broadcast(msg)
			if msg.Method == "playtime.limit.warning" || msg.Method == "playtime.limit.reached" {
				a.mu.Lock()
				media := append(json.RawMessage(nil), a.state["media"]...)
				a.mu.Unlock()
				go a.notifier.Notify(msg, media)
			}
			if msg.Method == "profiles.active" || msg.Method == "media.started" || msg.Method == "media.stopped" || msg.Method == "playtime.limit.reached" {
				callCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
				if err := a.refresh(callCtx); err != nil {
					log.Printf("State refresh: %v", err)
				}
				cancel()
			}
		case <-ticker.C:
			callCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
			if err := a.apply(callCtx); err != nil {
				log.Printf("Policy reconciliation: %v", err)
			}
			cancel()
		case <-live.C:
			callCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
			if result, err := a.core.Call(callCtx, "playtime", nil); err == nil {
				a.mu.Lock()
				a.state["playtime"] = result
				a.mu.Unlock()
				a.publishState()
			}
			cancel()
		}
	}
}

func validateConfig(c Config) error {
	if len(c.Token) < 32 {
		return fmt.Errorf("token must contain at least 32 characters")
	}
	u, err := url.Parse(c.CoreURL)
	if err != nil || u.Scheme != "ws" || net.ParseIP(u.Hostname()) == nil || !net.ParseIP(u.Hostname()).IsLoopback() {
		return fmt.Errorf("coreUrl must be a loopback ws:// address")
	}
	host, _, err := net.SplitHostPort(c.HookListen)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		return fmt.Errorf("hookListen must bind a loopback address")
	}
	return nil
}

func main() {
	path := flag.String("config", "config.json", "configuration file")
	generate := flag.Bool("generate-token", false, "print a new bearer token and exit")
	flag.Parse()
	if *generate {
		token := make([]byte, 32)
		if _, err := rand.Read(token); err != nil {
			log.Fatal(err)
		}
		fmt.Println(hex.EncodeToString(token))
		return
	}
	c := Config{Listen: ":7498", HookListen: "127.0.0.1:7499", CoreURL: "ws://127.0.0.1:7497/api/v0.1", StateFile: "state.json", KodiURL: "http://127.0.0.1:8080/jsonrpc", ESURL: "http://127.0.0.1:1234/notify", RetroArchAddress: "127.0.0.1:55355"}
	b, err := os.ReadFile(*path)
	if err != nil {
		log.Fatal(err)
	}
	if err = json.Unmarshal(b, &c); err != nil {
		log.Fatal(err)
	}
	if err = validateConfig(c); err != nil {
		log.Fatal(err)
	}
	if !filepath.IsAbs(c.StateFile) {
		c.StateFile = filepath.Join(filepath.Dir(*path), c.StateFile)
	}
	store, err := OpenStore(c.StateFile)
	if err != nil {
		log.Fatal(err)
	}
	a := newAgent(c, store)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	go a.Run(ctx)
	remote := &http.Server{Addr: c.Listen, Handler: a.remoteHandler(), ReadHeaderTimeout: 5 * time.Second}
	local := &http.Server{Addr: c.HookListen, Handler: a.hookHandler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := remote.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Print(err)
			cancel()
		}
	}()
	go func() {
		if err := local.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Print(err)
			cancel()
		}
	}()
	log.Printf("KidsStation agent 0.1.0 listening on %s", c.Listen)
	<-ctx.Done()
	shutdown, done := context.WithTimeout(context.Background(), 5*time.Second)
	defer done()
	_ = remote.Shutdown(shutdown)
	_ = local.Shutdown(shutdown)
}
