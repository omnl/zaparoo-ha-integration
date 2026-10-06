package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

type fakeCore struct {
	server  *httptest.Server
	mu      sync.Mutex
	updates []map[string]any
}

func startFakeCore(t *testing.T) *fakeCore {
	t.Helper()
	f := &fakeCore{}
	u := websocket.Upgrader{}
	f.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := u.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer ws.Close()
		for {
			var request Message
			if ws.ReadJSON(&request) != nil {
				return
			}
			var result any
			switch request.Method {
			case "profiles":
				result = map[string]any{"profiles": []any{map[string]any{"profileId": "child", "name": "Child", "switchId": "secret-card", "sessionLimit": "45m"}}}
			case "profiles.active":
				result = map[string]any{"profileId": "child", "name": "Child"}
			case "playtime":
				result = map[string]any{"dailyUsageToday": "12m", "dailyRemaining": "48m", "limitsEnabled": true}
			case "media":
				result = map[string]any{"active": []any{}}
			case "readers":
				result = map[string]any{"readers": []any{}}
			case "clients.current":
				result = map[string]any{"capabilities": []string{"profiles.manage"}}
			case "profiles.update":
				var params map[string]any
				_ = json.Unmarshal(request.Params, &params)
				f.mu.Lock()
				f.updates = append(f.updates, params)
				f.mu.Unlock()
				result = map[string]any{"profileId": "child", "name": "Child", "sessionLimit": "45m", "dailyLimit": params["dailyLimit"], "limitsEnabled": params["limitsEnabled"]}
			default:
				result = nil
			}
			encoded, _ := json.Marshal(result)
			if ws.WriteJSON(Message{JSONRPC: "2.0", ID: request.ID, Result: encoded}) != nil {
				return
			}
		}
	}))
	t.Cleanup(f.server.Close)
	return f
}

func testAgent(t *testing.T) (*Agent, *fakeCore) {
	t.Helper()
	f := startFakeCore(t)
	s, err := OpenStore(filepath.Join(t.TempDir(), "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	a := newAgent(Config{CoreURL: "ws" + strings.TrimPrefix(f.server.URL, "http"), Token: strings.Repeat("x", 64)}, s)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); a.Run(ctx) }()
	t.Cleanup(func() { cancel(); <-done })
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		a.mu.Lock()
		ready := a.synchronized
		a.mu.Unlock()
		if ready {
			return a, f
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("agent did not synchronize")
	return nil, nil
}

func callAgent(t *testing.T, a *Agent, method string, params any) json.RawMessage {
	t.Helper()
	body, _ := json.Marshal(params)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	result, err := a.handleRPC(ctx, method, body)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestAgentPolicyBonusAndRestore(t *testing.T) {
	a, f := testAgent(t)
	p := testPolicy()
	callAgent(t, a, "kidsstation.policy.set", p)
	callAgent(t, a, "kidsstation.add_time", map[string]any{"profileId": "child", "minutes": 30, "requestId": "same-grant"})
	callAgent(t, a, "kidsstation.add_time", map[string]any{"profileId": "child", "minutes": 30, "requestId": "same-grant"})
	f.mu.Lock()
	updates := append([]map[string]any(nil), f.updates...)
	f.mu.Unlock()
	if len(updates) != 2 {
		t.Fatalf("expected one policy and one grant write; got %d", len(updates))
	}
	if got := a.store.Snapshot().Bonuses["child"].Minutes; got != 30 {
		t.Fatalf("grant duplicated: %d", got)
	}
	p.Enabled = false
	callAgent(t, a, "kidsstation.policy.set", p)
	f.mu.Lock()
	last := f.updates[len(f.updates)-1]
	f.mu.Unlock()
	if last["clearLimits"] != true || last["sessionLimit"] != "45m" {
		t.Fatalf("original overrides not restored: %v", last)
	}
	if _, ok := last["dailyLimit"]; ok {
		t.Fatalf("inherited original daily limit was lost: %v", last)
	}
}

func TestBlockedDayAndDisconnectedHook(t *testing.T) {
	a, _ := testAgent(t)
	p := testPolicy()
	p.Minutes = [7]int{}
	callAgent(t, a, "kidsstation.policy.set", p)
	r := httptest.NewRequest(http.MethodGet, "/check-launch", nil)
	w := httptest.NewRecorder()
	a.hookHandler().ServeHTTP(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("zero-minute day allowed: %d", w.Code)
	}
	callAgent(t, a, "kidsstation.add_time", map[string]any{"profileId": "child", "minutes": 15, "requestId": "allow"})
	w = httptest.NewRecorder()
	a.hookHandler().ServeHTTP(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("bonus did not unlock day: %d %s", w.Code, w.Body.String())
	}
	a.mu.Lock()
	a.synchronized = false
	a.mu.Unlock()
	w = httptest.NewRecorder()
	a.hookHandler().ServeHTTP(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("unsynchronized launch allowed: %d", w.Code)
	}
}

func TestAuthenticatedWebsocketAndProfileRedaction(t *testing.T) {
	a, _ := testAgent(t)
	server := httptest.NewServer(a.remoteHandler())
	defer server.Close()
	response, err := http.Get(server.URL + "/v1/state")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatal("unauthenticated state exposed")
	}
	ws, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(server.URL, "http")+"/api/v0.1", http.Header{"Authorization": []string{"Bearer " + a.config.Token}})
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	_ = ws.WriteJSON(map[string]any{"jsonrpc": "2.0", "id": "test", "method": "profiles"})
	var reply Message
	if err = ws.ReadJSON(&reply); err != nil {
		t.Fatal(err)
	}
	if reply.Error != nil || strings.Contains(string(reply.Result), "secret-card") {
		t.Fatalf("invalid or unredacted reply: %s", reply.Result)
	}
}

func TestRejectIncompleteWeek(t *testing.T) {
	a, _ := testAgent(t)
	_, err := a.handleRPC(context.Background(), "kidsstation.policy.set", json.RawMessage(`{"profileId":"child","timezone":"Europe/Zurich","enabled":true,"minutes":[60]}`))
	if err == nil {
		t.Fatal("incomplete week accepted")
	}
}

func TestESNotificationUsesPlainText(t *testing.T) {
	var body string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Content-Type"), "text/plain") {
			t.Error("ES requires a text/plain body")
		}
		b := make([]byte, 1024)
		n, _ := r.Body.Read(b)
		body = string(b[:n])
	}))
	defer server.Close()
	n := Notifier{config: Config{ESURL: server.URL}, client: server.Client()}
	n.Notify(Message{Method: "playtime.limit.warning", Params: json.RawMessage(`{"remaining":"9m58s"}`)}, json.RawMessage(`{"active":[]}`))
	if body != "Noch 10 Minuten Medienzeit." {
		t.Fatalf("wrong notification: %q", body)
	}
}

func TestKodiMusicNotificationAndAuthentication(t *testing.T) {
	var received map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, password, ok := r.BasicAuth()
		if !ok || user != "parent" || password != "test-password" {
			t.Error("Kodi credentials missing")
		}
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			t.Error(err)
		}
		_, _ = w.Write([]byte(`{"jsonrpc":"2.0","id":1,"result":"OK"}`))
	}))
	defer server.Close()
	n := Notifier{config: Config{KodiURL: server.URL, KodiUser: "parent", KodiPassword: "test-password"}, client: server.Client()}
	n.Notify(Message{Method: "playtime.limit.warning", Params: json.RawMessage(`{"remaining":"59s"}`)}, json.RawMessage(`{"active":[{"systemId":"MusicTrack","launcherId":"Kodi"}]}`))
	if received["method"] != "GUI.ShowNotification" {
		t.Fatalf("Kodi did not receive a notification: %v", received)
	}
}

func TestRetroArchReceivesNetworkOSD(t *testing.T) {
	conn, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	es := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer es.Close()
	n := Notifier{config: Config{RetroArchAddress: conn.LocalAddr().String(), ESURL: es.URL}, client: es.Client()}
	n.Notify(Message{Method: "playtime.limit.warning", Params: json.RawMessage(`{"remaining":"4m58s"}`)}, json.RawMessage(`{"active":[{"systemId":"SNES"}]}`))
	_ = conn.SetReadDeadline(time.Now().Add(time.Second))
	buf := make([]byte, 256)
	count, _, err := conn.ReadFrom(buf)
	if err != nil {
		t.Fatal(err)
	}
	if string(buf[:count]) != "SHOW_MSG Noch 5 Minuten Medienzeit.\n" {
		t.Fatalf("incorrect RetroArch command: %q", buf[:count])
	}
}
