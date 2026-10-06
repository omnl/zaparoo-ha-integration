# KidsStation releases

## v0.1: combined daily allowance

- Current Core event names, RPC errors, reconnect snapshots and connection binary sensor.
- Profile devices, HA-person links, current Core playtime and media category.
- Local Go agent with persistent weekday schedules, expiring daily bonus and idempotent grants.
- Bonus buttons and validated actions scoped to a device and a profile/person.
- Local zero-day launch guard and Kodi / RetroArch / EmulationStation warnings.

## Next: weekly and category budgets

Add a persistent usage ledger pinned to each launch's profile ID, checkpoint active sessions, reconcile across restarts and midnight, and enforce weekly and category allowances. Expose gaming/video/audio usage and weekly remaining sensors, then add a dashboard card.

**Core admission ordering must be resolved before category enforcement.** In the inspected Core source, queue admission checks playtime before the ZapScript `before_media_start` hook executes. Therefore, that hook alone cannot raise an already exhausted category-dependent daily limit to admit a different category. A Core integration or a pre-admission mechanism with resolved launch context is required; v0.1 intentionally uses a combined allowance. See Core `pkg/service/queues.go` and `pkg/service/playtime/limits.go`.

Add a universal overlay for standalone emulators only after the Kodi and RetroArch paths have been validated on the device.
