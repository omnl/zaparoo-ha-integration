package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net"
	"net/http"
	"strings"
	"time"
)

type Notifier struct {
	config Config
	client *http.Client
}

func category(media map[string]any) string {
	system, _ := media["systemId"].(string)
	launcher, _ := media["launcherId"].(string)
	if strings.Contains(strings.ToLower(launcher), "kodi") {
		return "video"
	}
	switch system {
	case "Movie", "TVShow", "TVEpisode", "Video":
		return "video"
	case "MusicTrack", "MusicAlbum", "MusicArtist", "Audio":
		return "audio"
	case "":
		return "idle"
	default:
		return "gaming"
	}
}

func (n *Notifier) Notify(msg Message, raw json.RawMessage) {
	text := "Deine Medienzeit ist aufgebraucht."
	if msg.Method == "playtime.limit.warning" {
		var params struct {
			Remaining string `json:"remaining"`
		}
		if json.Unmarshal(msg.Params, &params) != nil {
			return
		}
		remaining, err := time.ParseDuration(params.Remaining)
		if err != nil {
			return
		}
		minutes := int(math.Ceil(remaining.Minutes()))
		if minutes < 1 {
			text = "Noch weniger als 1 Minute Medienzeit."
		} else if minutes == 1 {
			text = "Noch 1 Minute Medienzeit."
		} else {
			text = fmt.Sprintf("Noch %d Minuten Medienzeit.", minutes)
		}
	}
	var state struct {
		Active []map[string]any `json:"active"`
	}
	_ = json.Unmarshal(raw, &state)
	media := map[string]any{}
	for _, item := range state.Active {
		if item["slot"] != "background" {
			media = item
			break
		}
	}
	launcher, _ := media["launcherId"].(string)
	if category(media) == "video" || strings.Contains(strings.ToLower(launcher), "kodi") {
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "GUI.ShowNotification", "params": map[string]any{"title": "KidsStation", "message": text, "displaytime": 10000}})
		request, err := http.NewRequest(http.MethodPost, n.config.KodiURL, bytes.NewReader(body))
		if err == nil {
			request.Header.Set("Content-Type", "application/json")
			if n.config.KodiUser != "" {
				request.SetBasicAuth(n.config.KodiUser, n.config.KodiPassword)
			}
			response, err := n.client.Do(request)
			if err == nil {
				defer response.Body.Close()
				if response.StatusCode == http.StatusOK {
					return
				}
			}
		}
	}
	if category(media) == "gaming" {
		// Only RetroArch provides this network OSD; other emulators fall through to ES.
		conn, err := net.DialTimeout("udp", n.config.RetroArchAddress, time.Second)
		if err == nil {
			_, _ = conn.Write([]byte("SHOW_MSG " + text + "\n"))
			_ = conn.Close()
		}
	}
	request, err := http.NewRequest(http.MethodPost, n.config.ESURL, strings.NewReader(text))
	if err != nil {
		return
	}
	request.Header.Set("Content-Type", "text/plain; charset=utf-8")
	response, err := n.client.Do(request)
	if err != nil {
		log.Printf("EmulationStation notification: %v", err)
		return
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 {
		log.Printf("EmulationStation notification returned HTTP %d", response.StatusCode)
	}
}
