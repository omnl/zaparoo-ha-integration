# v0.1 validation

Validated on 2026-10-06.

| Check | Result |
| --- | --- |
| Python integration tests, HA 2025.2.4 / Python 3.13.15 | 17 passed |
| Python integration tests, HA 2026.9.4 / Python 3.14.7 | 17 passed |
| Go agent tests with race detector, Go 1.27.1 | 11 passed |
| Python Ruff lint and formatting | Passed |
| Shell installer/service/build syntax | Passed |
| JSON/YAML parsing and Git whitespace check | Passed |
| Static Linux amd64 and arm64 agent builds | Passed |

The 28 distinct test cases cover real WebSocket RPC replies and errors, pending-request cleanup, reconnect snapshots, null notifications, event de-duplication, local weekday and DST boundaries, bonus expiry, persistent and idempotent grants, concurrent writes, failed storage writes, zero-day and disconnected launch guards, token authentication, credential redaction, Kodi/RetroArch/ES notification protocols, HA service routing and person selector options. The Python process-level test starts the compiled Go binary, sends policy and bonus through the actual HA transport, restarts the agent, and checks persisted state and Core writes.

Core itself is simulated in automated tests. Installation, shutdown/enforcement and visible OSD on the actual Batocera/Kodi/RetroArch hardware have not yet been validated. Weekly and separate category budgets are future work; see [ROADMAP.md](ROADMAP.md).

## Reference source states

- HA integration base: `ZaparooProject/zaparoo-ha-integration` commit `7ec733ec0c47dea608f9e499f8e0a6fc9d1b9b39`.
- Inspected Core API/admission source: `ZaparooProject/zaparoo-core` commit `61cbe491e0df973a5b2be6ce6a0d3929ccc298ce`.
- Inspected Batocera launcher/service source: `batocera-linux/batocera.linux` commit `0803104d73294723010505f49f6b70b4ba4346d1`.
- ES notification endpoint: `batocera-linux/batocera-emulationstation`, `es-app/src/services/HttpServerThread.cpp`.
