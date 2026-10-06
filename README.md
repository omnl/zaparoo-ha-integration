# KidsStation (Zaparoo) — v0.1

A fork of [ZaparooProject/zaparoo-ha-integration](https://github.com/ZaparooProject/zaparoo-ha-integration) for a Batocera kids media station. Home Assistant provides parent controls; a local Go agent schedules Zaparoo profile limits and displays warnings.

## Included

- Current Core events, JSON-RPC error handling, reconnect snapshots and a connection binary sensor.
- Zaparoo profile devices linked to existing Home Assistant persons.
- One combined daily allowance per profile, with independent values for Monday through Sunday.
- Persistent daily bonus time, +15/+30/+60 buttons, automatic local-midnight expiry and idempotent grants.
- Local scheduling when HA is offline, a fail-closed launch hook for zero-minute days, and Kodi/RetroArch/EmulationStation warnings.
- Existing launch, stop and media actions; profile switching and schedule/bonus actions.

Gaming/video/audio categories are shown for inspection. Independent category limits and weekly budgets are the next release, described in [the roadmap](docs/ROADMAP.md).

## Install

Start with [the German installation guide](docs/INSTALL_DE.md). Copy `custom_components/zaparoo` into Home Assistant and install the agent on Batocera using the release package. The integration keeps the `zaparoo` domain and replaces the upstream component; it is not an HA Supervisor app/add-on.

The agent requires a locally running Core with profile management, `clients.current` and media launch hooks. It speaks to Core over loopback; Zaparoo remote encryption can stay enabled. The LAN-facing HA connection requires a bearer token. Use your existing Home Assistant remote access for parent controls.

No profile is scheduled until a parent configures it. HA-person links are metadata; NFC profile scans remain the way to identify a child at the station. Core retains playtime measurement and stopping media at its limit.

## Actions

| Action | Purpose |
| --- | --- |
| `zaparoo.launch` | Emulate an NFC/token scan |
| `zaparoo.stop` | Stop the current launcher |
| `zaparoo.media` | Return the current media snapshot |
| `zaparoo.switch_profile` | Activate a profile, supplying its PIN when needed |
| `zaparoo.set_schedule` | Set seven daily minute values, Monday first |
| `zaparoo.add_time` | Add minutes to today's allowance |

Actions target a KidsStation device. Profile actions accept `profile_id` or a uniquely linked `person`. Bonus adds to the daily limit; a separately configured Core session limit remains in effect.

## Develop and verify

Python 3.13, Go 1.24+:

```sh
python -m pip install -r requirements.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
cd agent
go test -race ./...
cd ..
sh scripts/build-agent
KIDSSTATION_AGENT_BINARY="$PWD/dist/kidsstation-agent-linux-amd64" python -m pytest -q
```

Tests use real WebSockets and simulated Core responses. The process-level test additionally runs the compiled Go agent against the HA Python client. Hardware validation on Batocera is still required before household use.

## License

GPL-3.0, inherited from the upstream repository. See [LICENSE](LICENSE).
