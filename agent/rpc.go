package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (e *RPCError) Error() string { return fmt.Sprintf("RPC %d: %s", e.Code, e.Message) }

type Message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *RPCError       `json:"error,omitempty"`
}

type Core struct {
	URL      string
	mu       sync.Mutex
	write    sync.Mutex
	conn     *websocket.Conn
	pending  map[string]chan Message
	sequence atomic.Uint64
}

func (c *Core) Call(ctx context.Context, method string, params any) (json.RawMessage, error) {
	id, _ := json.Marshal(fmt.Sprint(c.sequence.Add(1)))
	req := Message{JSONRPC: "2.0", ID: id, Method: method}
	if params != nil {
		req.Params, _ = json.Marshal(params)
	}
	reply := make(chan Message, 1)
	c.mu.Lock()
	ws := c.conn
	if ws == nil {
		c.mu.Unlock()
		return nil, errors.New("Zaparoo is disconnected")
	}
	c.pending[string(id)] = reply
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, string(id)); c.mu.Unlock() }()
	c.write.Lock()
	_ = ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	err := ws.WriteJSON(req)
	c.write.Unlock()
	if err != nil {
		return nil, err
	}
	select {
	case response := <-reply:
		if response.Error != nil {
			return nil, response.Error
		}
		return response.Result, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (c *Core) disconnect(ws *websocket.Conn) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != ws {
		return
	}
	c.conn = nil
	for _, ch := range c.pending {
		select {
		case ch <- Message{Error: &RPCError{-32001, "Zaparoo disconnected"}}:
		default:
		}
	}
	c.pending = map[string]chan Message{}
	_ = ws.Close()
}

func (c *Core) Run(ctx context.Context, ready func(context.Context) error, event func(Message), offline func()) {
	backoff := time.Second
	for ctx.Err() == nil {
		ws, _, err := websocket.DefaultDialer.DialContext(ctx, c.URL, nil)
		if err == nil {
			c.mu.Lock()
			c.conn = ws
			c.pending = map[string]chan Message{}
			c.mu.Unlock()
			ws.SetReadLimit(2 << 20)
			done := make(chan struct{})
			go func() {
				defer close(done)
				defer c.disconnect(ws)
				for {
					var msg Message
					if ws.ReadJSON(&msg) != nil {
						return
					}
					if len(msg.ID) > 0 {
						c.mu.Lock()
						ch := c.pending[string(msg.ID)]
						if ch != nil {
							select {
							case ch <- msg:
							default:
							}
						}
						c.mu.Unlock()
					} else if msg.Method != "" {
						event(msg)
					}
				}
			}()
			setupCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			err = ready(setupCtx)
			cancel()
			if err == nil {
				backoff = time.Second
				select {
				case <-done:
				case <-ctx.Done():
				}
			} else {
				log.Printf("Core synchronization failed: %v", err)
			}
			c.disconnect(ws)
			<-done
		}
		offline()
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
}
