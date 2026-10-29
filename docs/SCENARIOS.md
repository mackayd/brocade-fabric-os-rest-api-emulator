# Scenario engine

The scenario endpoint changes only the in-memory emulator profile. `recovery` normalizes to `healthy`.

| Category | Scenarios |
|---|---|
| Baseline | `healthy`, `recovery` |
| Switch | `reboot`, `switch-degraded`, `switch-critical`, `switch-unavailable` |
| Port/ISL | `port-admin-down`, `port-unexpected-down`, `isl-down` |
| Counters | `crc-errors`, `link-errors`, `loss-of-signal`, `loss-of-sync`, `invalid-words`, `high-utilisation`, `congestion`, `slow-drain` |
| Hardware/optics | `psu-failed`, `fan-failed`, `high-temperature`, `optic-warning`, `optic-failed` |
| Transport/parser | `auth-failure`, `http-500`, `timeout`, `malformed-payload`, `partial-payload` |

The same scenario names may affect model-specific port speeds, optics, fan counts, and sensor values differently because each personality has its own profile.
